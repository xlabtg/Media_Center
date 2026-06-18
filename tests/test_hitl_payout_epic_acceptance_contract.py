from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from hitl_payout_gateway import (
    HITLPayoutAPIState,
    InMemoryBlockchainAuditConnector,
    InMemoryNotificationConnector,
    InMemoryPaymentConnector,
    create_hitl_payout_app,
)

from libs.shared import ServiceTemplateConfig, TOTPService, encode_hs256_jwt

ROOT = Path(__file__).resolve().parents[1]
JWT_SECRET = "hitl-payout-issue-43-secret"
TOTP_SECRET = "JBSWY3DPEHPK3PXP"


def test_issue_43_hitl_payout_epic_acceptance_contract() -> None:
    app = _app()
    client = TestClient(app)
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    confirmation_at = now + timedelta(minutes=1)

    queued = client.post(
        "/payouts/queue",
        headers=_headers(subject="council-1"),
        json=_queue_payload(
            payout_id="payout-issue-43-pass",
            event_id="evt-issue-43-pass-queued",
            now=now,
            distribution_hash="d" * 64,
        ),
    )
    execute_without_2fa = client.post(
        "/payouts/payout-issue-43-pass/execute",
        headers=_headers(subject="council-2"),
        json={"now": (now + timedelta(hours=8, minutes=1)).isoformat()},
    )
    confirmed = client.post(
        "/payouts/payout-issue-43-pass/confirm",
        headers=_headers(subject="council-2"),
        json={
            "confirmation_id": "confirmation-issue-43",
            "event_id": "evt-issue-43-confirmed",
            "totp_code": _totp_code(confirmation_at),
            "confirmed_at": int(confirmation_at.timestamp()),
            "metadata": {"issue": "43"},
        },
    )
    execute_before_veto_window = client.post(
        "/payouts/payout-issue-43-pass/execute",
        headers=_headers(subject="council-2"),
        json={"now": (now + timedelta(hours=7, minutes=59)).isoformat()},
    )
    executed = client.post(
        "/payouts/payout-issue-43-pass/execute",
        headers=_headers(subject="council-2"),
        json={
            "execution_id": "execution-issue-43",
            "event_id": "evt-issue-43-executed",
            "notification_id": "notification-issue-43",
            "now": (now + timedelta(hours=8, minutes=1)).isoformat(),
            "metadata": {"issue": "43"},
        },
    )

    assert queued.status_code == 201
    queued_body = queued.json()
    assert queued_body["status"] == "queued"
    assert queued_body["requires_2fa"] is True
    assert queued_body["veto_until"] == "2026-06-18T20:00:00Z"
    assert execute_without_2fa.status_code == 409
    assert execute_without_2fa.json()["error"]["code"] == "payout_not_executable"
    assert confirmed.status_code == 200
    assert confirmed.json()["confirmation_id"] == "confirmation-issue-43"
    assert execute_before_veto_window.status_code == 409
    assert execute_before_veto_window.json()["error"]["code"] == (
        "payout_not_executable"
    )
    assert executed.status_code == 200

    executed_body = executed.json()
    assert executed_body["execution_id"] == "execution-issue-43"
    assert executed_body["payout_id"] == "payout-issue-43-pass"
    assert executed_body["notification_id"] == "notification-issue-43"
    assert executed_body["audit_chain_ref"] == "audit-chain-evt-issue-43-executed"

    fetched = client.get(
        "/payouts/payout-issue-43-pass",
        headers=_headers(subject="council-1"),
    )
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "executed"

    state = cast(HITLPayoutAPIState, app.state.hitl_payout_api)
    payment_connector = cast(
        InMemoryPaymentConnector,
        state.execution_manager.payment_connector,
    )
    blockchain_auditor = cast(
        InMemoryBlockchainAuditConnector,
        state.execution_manager.blockchain_auditor,
    )
    notification_connector = cast(
        InMemoryNotificationConnector,
        state.execution_manager.notification_connector,
    )

    assert [record.event_type for record in state.audit_log_sink.records] == [
        "payout.queued",
        "payout.confirmed",
        "payout.executed",
    ]
    assert [message.envelope.type for message in state.publisher.messages] == [
        "payout.queued",
        "payout.confirmed",
        "payout.executed",
    ]
    assert len(payment_connector.commands) == 1
    assert payment_connector.commands[0].payout_id == "payout-issue-43-pass"
    assert len(blockchain_auditor.records) == 1
    assert blockchain_auditor.records[0].audit_hash == executed_body["audit_hash"]
    assert "member_id" not in blockchain_auditor.records[0].metadata
    assert "payout_share" not in blockchain_auditor.records[0].metadata
    assert len(notification_connector.notifications) == 1
    assert notification_connector.notifications[0].recipient_id == "member-1"


def test_issue_43_hitl_payout_veto_acceptance_contract() -> None:
    app = _app()
    client = TestClient(app)
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)

    queued = client.post(
        "/payouts/queue",
        headers=_headers(subject="council-1"),
        json=_queue_payload(
            payout_id="payout-issue-43-veto",
            event_id="evt-issue-43-veto-queued",
            now=now,
            distribution_hash="e" * 64,
        ),
    )
    vetoed = client.post(
        "/payouts/payout-issue-43-veto/veto",
        headers=_headers(subject="council-2"),
        json={
            "decision_id": "veto-issue-43",
            "event_id": "evt-issue-43-vetoed",
            "reason_code": "policy_mismatch",
            "reason": "Совет остановил выплату в пределах окна вето",
            "now": (now + timedelta(hours=2)).isoformat(),
        },
    )
    execute_vetoed = client.post(
        "/payouts/payout-issue-43-veto/execute",
        headers=_headers(subject="council-2"),
        json={"now": (now + timedelta(hours=8, minutes=1)).isoformat()},
    )
    canceled = client.get(
        "/payouts",
        headers=_headers(subject="council-1"),
        params={"status": "canceled"},
    )

    assert queued.status_code == 201
    assert vetoed.status_code == 200
    assert vetoed.json()["decision_id"] == "veto-issue-43"
    assert execute_vetoed.status_code == 409
    assert execute_vetoed.json()["error"]["code"] == "payout_not_executable"
    assert canceled.status_code == 200
    assert [item["payout_id"] for item in canceled.json()["items"]] == [
        "payout-issue-43-veto"
    ]
    assert canceled.json()["items"][0]["veto_decision_id"] == "veto-issue-43"

    state = cast(HITLPayoutAPIState, app.state.hitl_payout_api)
    assert [record.event_type for record in state.audit_log_sink.records] == [
        "payout.queued",
        "payout.vetoed",
    ]
    assert [message.envelope.type for message in state.publisher.messages] == [
        "payout.queued",
        "payout.vetoed",
    ]


def test_issue_43_hitl_payout_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/hitl-payout-gateway.md").read_text(encoding="utf-8")

    for marker in (
        "**Статус:** 🟢 реализовано",
        "queue_manager",
        "veto_manager",
        "confirmation_manager",
        "execution_manager",
        "create_hitl_payout_app",
        "#39",
        "#40",
        "#41",
        "#42",
        "#43",
        "Спецификация синхронизирована с реализацией HITL Payout Gateway",
    ):
        assert marker in spec


def _app() -> FastAPI:
    return create_hitl_payout_app(
        ServiceTemplateConfig(
            service_name="hitl-payout-gateway",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        ),
        veto_window_hours=8,
        totp_secrets={("tenant-a", "council-2"): TOTP_SECRET},
    )


def _headers(
    *,
    tenant_id: str = "tenant-a",
    subject: str,
    roles: tuple[str, ...] = ("council",),
    correlation_id: str = "corr-issue-43",
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


def _queue_payload(
    *,
    payout_id: str,
    event_id: str,
    now: datetime,
    distribution_hash: str,
) -> dict[str, object]:
    return {
        "payout_id": payout_id,
        "event_id": event_id,
        "member_id": "member-1",
        "period": "2026-06",
        "payout_share": 0.25,
        "distribution_id": "distribution-issue-43",
        "distribution_hash": distribution_hash,
        "now": now.isoformat().replace("+00:00", "Z"),
    }


def _totp_code(at: datetime) -> str:
    totp = TOTPService(clock=lambda: at.timestamp())
    return totp.generate_totp(TOTP_SECRET)
