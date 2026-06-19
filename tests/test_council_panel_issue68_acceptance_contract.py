from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from hitl_payout_gateway import (
    InMemoryPayoutQueueRepository,
    PayoutConfirmationManager,
    PayoutQueueManager,
    PayoutStatus,
    VetoManager,
)
from policy_manager import InMemoryPolicyRepository, PolicyManager
from web_cabinet import (
    CouncilPanelAuditRecord,
    CouncilPanelPayoutAnnotation,
    InMemoryCouncilPanelRepository,
    WebCabinetAPIState,
    create_web_cabinet_app,
)

from libs.shared import ServiceTemplateConfig, TOTPService, encode_hs256_jwt

ROOT = Path(__file__).resolve().parents[1]
JWT_SECRET = "council-panel-issue-68-secret"
TOTP_SECRET = "JBSWY3DPEHPK3PXP"
PERIOD = "2026-06"


def test_issue_68_council_panel_covers_hitl_veto_policies_and_2fa() -> None:
    app = _app()
    _seed_payouts(app)
    client = TestClient(app)
    now = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)

    overview = client.get(
        "/council/panel/overview",
        headers=_headers(subject="council-1"),
        params={"now": now.isoformat()},
    )
    html = client.get(
        "/council/panel",
        headers=_headers(subject="council-1"),
        params={"now": now.isoformat()},
    )

    assert overview.status_code == 200
    body = overview.json()
    assert body["tenant_id"] == "tenant-a"
    assert body["role"] == "council"
    assert body["two_factor"]["active"] is True
    assert body["summary"] == {
        "queued": 2,
        "ready_to_execute": 0,
        "canceled": 0,
        "executed": 0,
        "requires_2fa": 2,
        "veto_window_expiring": 1,
    }
    assert [item["payout_id"] for item in body["payouts"]] == [
        "payout-high-risk",
        "payout-normal",
    ]
    high_risk = body["payouts"][0]
    assert high_risk["risk_level"] == "high"
    assert high_risk["veto_available"] is True
    assert high_risk["confirm_available"] is True
    assert high_risk["policy_key"] == "hitl.veto_window_hours"
    assert high_risk["policy_version"] == 1
    assert high_risk["calculation"]["source"] == "payout_distribution"
    assert high_risk["audit_timeline"] == [
        {
            "event_type": "payout.queued",
            "event_id": "evt-panel-high-queued",
            "audit_hash": "a" * 64,
            "occurred_at": "2026-06-19T04:30:00Z",
        }
    ]
    assert body["policies"][0]["key"] == "hitl.veto_window_hours"
    assert body["policies"][0]["value"]["default"] == 8
    assert "tenant-b" not in overview.text

    assert html.status_code == 200
    assert html.headers["content-type"].startswith("text/html")
    assert '<meta name="viewport"' in html.text
    assert "@media (max-width: 760px)" in html.text
    assert "Панель Совета" in html.text
    assert "payout-high-risk" in html.text
    assert "HIGH" in html.text
    assert "2FA active" in html.text
    assert "Вето" in html.text
    assert "Подтвердить" in html.text
    assert "tenant-b" not in html.text

    vetoed = client.post(
        "/council/payouts/payout-high-risk/veto",
        headers=_headers(subject="council-2"),
        json={
            "decision_id": "veto-panel-high",
            "event_id": "evt-panel-high-vetoed",
            "reason_code": "policy_mismatch",
            "reason": "Совет остановил выплату в пределах окна вето",
            "now": (now + timedelta(minutes=15)).isoformat(),
        },
    )
    assert vetoed.status_code == 200
    assert vetoed.json()["decision_id"] == "veto-panel-high"

    policy = client.put(
        "/council/policies/hitl.veto_window_hours",
        headers=_headers(subject="council-1"),
        json={
            "value": {
                "kind": "threshold",
                "target": "veto_window_hours",
                "operator": "between",
                "min": 6,
                "max": 12,
                "default": 10,
                "reason": "veto_window_out_of_range",
                "decision_on_violation": "escalate",
            },
            "updated_at": "2026-06-19T12:20:00Z",
            "metadata": {"issue": "68", "decision": "council-panel"},
        },
    )
    assert policy.status_code == 200
    assert policy.json()["key"] == "hitl.veto_window_hours"
    assert policy.json()["version"] == 2
    assert policy.json()["updated_by"] == "council-1"

    confirm_without_2fa = client.post(
        "/council/payouts/payout-normal/confirm",
        headers=_headers(subject="council-2"),
        json={
            "confirmation_id": "confirmation-without-code",
            "confirmed_at": int((now + timedelta(minutes=30)).timestamp()),
        },
    )
    assert confirm_without_2fa.status_code == 400
    assert confirm_without_2fa.json()["error"]["code"] == "validation_error"

    confirmation_at = now + timedelta(minutes=30)
    confirmed = client.post(
        "/council/payouts/payout-normal/confirm",
        headers=_headers(subject="council-2"),
        json={
            "confirmation_id": "confirmation-panel-normal",
            "event_id": "evt-panel-normal-confirmed",
            "totp_code": _totp_code(confirmation_at),
            "confirmed_at": int(confirmation_at.timestamp()),
            "metadata": {"issue": "68", "source": "council-panel"},
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["confirmation_id"] == "confirmation-panel-normal"
    assert confirmed.json()["actor_id"] == "council-2"
    assert confirmed.json()["operation"] == "payout.confirm"

    state = cast(WebCabinetAPIState, app.state.web_cabinet_api)
    assert (
        state.payout_queue_manager.get_payout(
            tenant_id="tenant-a",
            payout_id="payout-high-risk",
        ).status
        is PayoutStatus.CANCELED
    )
    assert (
        state.payout_queue_manager.get_payout(
            tenant_id="tenant-a",
            payout_id="payout-normal",
        ).confirmation_id
        == "confirmation-panel-normal"
    )


def test_issue_68_council_panel_enforces_rbac_and_tenant_context() -> None:
    app = _app()
    _seed_payouts(app)
    client = TestClient(app)

    forbidden = client.get(
        "/council/panel/overview",
        headers=_headers(subject="member-1", roles=("member_full",)),
    )
    headers = _headers(subject="council-1", roles=("council",))
    headers["X-Tenant-Id"] = "tenant-b"
    tenant_override = client.get("/council/panel/overview", headers=headers)

    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "forbidden"
    assert tenant_override.status_code == 403
    assert tenant_override.json()["error"]["code"] == "tenant_isolation_violation"

    state = cast(WebCabinetAPIState, app.state.web_cabinet_api)
    assert state.tenant_audit_sink.events[0].event_type == "tenant.isolation_violation"
    assert state.tenant_audit_sink.events[0].tenant_id == "tenant-a"


def test_issue_68_council_panel_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/web-cabinet.md").read_text(encoding="utf-8")
    activity_spec = (ROOT / "docs/modules/activity-command-center.md").read_text(
        encoding="utf-8"
    )
    readme = (ROOT / "services/web-cabinet/README.md").read_text(encoding="utf-8")

    for marker in (
        "реализовано для #67 и #68",
        "GET** `/council/panel/overview`",
        "GET** `/council/panel`",
        "POST** `/council/payouts/{payout_id}/veto`",
        "POST** `/council/payouts/{payout_id}/confirm`",
        "PUT** `/council/policies/{key}`",
        "tenant-isolation контракт #68",
    ):
        assert marker in spec

    for marker in (
        "#68 реализует клиентский экран",
        "очередь HITL",
        "2FA-dialog",
    ):
        assert marker in activity_spec

    for marker in (
        "Панель Совета",
        "GET /council/panel/overview",
        "InMemoryCouncilPanelRepository",
    ):
        assert marker in readme


def _app() -> FastAPI:
    payout_repository = InMemoryPayoutQueueRepository()
    return create_web_cabinet_app(
        ServiceTemplateConfig(
            service_name="web-cabinet",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        ),
        payout_queue_manager=PayoutQueueManager(
            repository=payout_repository,
            veto_window_hours=8,
        ),
        veto_manager=VetoManager(repository=payout_repository),
        confirmation_manager=PayoutConfirmationManager(repository=payout_repository),
        policy_manager=PolicyManager(repository=InMemoryPolicyRepository()),
        council_panel_repository=InMemoryCouncilPanelRepository(),
        totp_secrets={
            ("tenant-a", "council-1"): TOTP_SECRET,
            ("tenant-a", "council-2"): TOTP_SECRET,
        },
    )


def _headers(
    *,
    tenant_id: str = "tenant-a",
    subject: str,
    roles: tuple[str, ...] = ("council",),
    correlation_id: str = "corr-council-panel-issue-68",
) -> dict[str, str]:
    token = encode_hs256_jwt(
        {
            "tenant_id": tenant_id,
            "sub": subject,
            "roles": list(roles),
        },
        JWT_SECRET,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": tenant_id,
        "X-Correlation-Id": correlation_id,
    }


def _totp_code(at: datetime) -> str:
    totp = TOTPService(clock=lambda: at.timestamp())
    return totp.generate_totp(TOTP_SECRET)


def _seed_payouts(app: FastAPI) -> None:
    state = cast(WebCabinetAPIState, app.state.web_cabinet_api)
    for payout_id, member_id, payout_share, distribution_hash, queued_at in (
        (
            "payout-high-risk",
            "member-high",
            0.45,
            "b" * 64,
            datetime(2026, 6, 19, 4, 30, tzinfo=UTC),
        ),
        (
            "payout-normal",
            "member-normal",
            0.12,
            "c" * 64,
            datetime(2026, 6, 19, 8, 0, tzinfo=UTC),
        ),
    ):
        asyncio.run(
            state.payout_queue_manager.queue_payout(
                tenant_id="tenant-a",
                member_id=member_id,
                period=PERIOD,
                payout_share=payout_share,
                distribution_id=f"distribution-{payout_id}",
                distribution_hash=distribution_hash,
                created_by="council-1",
                correlation_id="corr-seed-issue-68",
                payout_id=payout_id,
                event_id=f"evt-{payout_id}-queued",
                now=queued_at,
            )
        )

    state.council_panel_repository.save_annotation(
        CouncilPanelPayoutAnnotation(
            tenant_id="tenant-a",
            payout_id="payout-high-risk",
            risk_level="high",
            risk_reason="Новый получатель и высокая доля выплаты",
            policy_key="hitl.veto_window_hours",
            calculation_source="payout_distribution",
            calculation_explanation="Кв ограничен 0.10, доля взята из snapshot",
        )
    )
    state.council_panel_repository.save_annotation(
        CouncilPanelPayoutAnnotation(
            tenant_id="tenant-a",
            payout_id="payout-normal",
            risk_level="medium",
            risk_reason="Плановая выплата",
            policy_key="hitl.veto_window_hours",
            calculation_source="payout_distribution",
            calculation_explanation="Плановый snapshot распределения",
        )
    )
    state.council_panel_repository.save_annotation(
        CouncilPanelPayoutAnnotation(
            tenant_id="tenant-b",
            payout_id="payout-other-tenant",
            risk_level="high",
            risk_reason="Эта запись не должна попасть в tenant-a",
            policy_key="hitl.veto_window_hours",
            calculation_source="payout_distribution",
            calculation_explanation="tenant-b",
        )
    )
    state.council_panel_repository.add_audit_record(
        CouncilPanelAuditRecord(
            tenant_id="tenant-a",
            payout_id="payout-high-risk",
            event_type="payout.queued",
            event_id="evt-panel-high-queued",
            audit_hash="a" * 64,
            occurred_at=datetime(2026, 6, 19, 4, 30, tzinfo=UTC),
        )
    )
