from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from hitl_payout_gateway import HITLPayoutAPIState, create_hitl_payout_app

from libs.shared import ServiceTemplateConfig, TOTPService, encode_hs256_jwt

JWT_SECRET = "hitl-payout-test-secret"
TOTP_SECRET = "JBSWY3DPEHPK3PXP"


def _app(*, veto_window_hours: int = 8) -> FastAPI:
    config = ServiceTemplateConfig(
        service_name="hitl-payout-gateway",
        version="0.1.0",
        jwt_secret=JWT_SECRET,
        prometheus_enabled=True,
    )
    return create_hitl_payout_app(
        config,
        veto_window_hours=veto_window_hours,
        totp_secrets={("tenant-a", "council-2"): TOTP_SECRET},
    )


def _client(*, veto_window_hours: int = 8) -> TestClient:
    return TestClient(_app(veto_window_hours=veto_window_hours))


def _headers(
    *,
    tenant_id: str = "tenant-a",
    subject: str = "council-1",
    roles: tuple[str, ...] = ("council",),
    correlation_id: str = "corr-payout-1",
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
    now: datetime,
    payout_id: str = "payout-1",
    distribution_hash: str = "a" * 64,
) -> dict[str, object]:
    return {
        "payout_id": payout_id,
        "event_id": f"evt-{payout_id}-queued",
        "member_id": "member-1",
        "period": "2026-06",
        "payout_share": 0.25,
        "distribution_id": "distribution-1",
        "distribution_hash": distribution_hash,
        "now": now.isoformat().replace("+00:00", "Z"),
    }


def _totp_code(at: datetime) -> str:
    totp = TOTPService(clock=lambda: at.timestamp())
    return totp.generate_totp(TOTP_SECRET)


def test_hitl_payout_openapi_documents_public_endpoints() -> None:
    client = _client()

    response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["paths"].keys() >= {
        "/payouts/queue",
        "/payouts",
        "/payouts/{payout_id}",
        "/payouts/{payout_id}/veto",
        "/payouts/{payout_id}/confirm",
        "/payouts/{payout_id}/execute",
    }


def test_hitl_payout_api_confirms_and_executes_after_veto_window() -> None:
    app = _app()
    client = TestClient(app)
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    confirmation_at = now + timedelta(minutes=1)

    queued = client.post(
        "/payouts/queue",
        headers=_headers(subject="council-1"),
        json=_queue_payload(now=now),
    )

    assert queued.status_code == 201
    queued_body = queued.json()
    assert queued_body["tenant_id"] == "tenant-a"
    assert queued_body["status"] == "queued"
    assert queued_body["created_by"] == "council-1"
    assert queued_body["veto_until"] == "2026-06-18T20:00:00Z"

    confirmed = client.post(
        "/payouts/payout-1/confirm",
        headers=_headers(subject="council-2"),
        json={
            "confirmation_id": "confirmation-1",
            "event_id": "evt-payout-confirmed-1",
            "totp_code": _totp_code(confirmation_at),
            "confirmed_at": int(confirmation_at.timestamp()),
            "metadata": {"approval_source": "api-test"},
        },
    )

    assert confirmed.status_code == 200
    confirmed_body = confirmed.json()
    assert confirmed_body["confirmation_id"] == "confirmation-1"
    assert confirmed_body["actor_id"] == "council-2"
    assert confirmed_body["actor_role"] == "council"

    early_execution = client.post(
        "/payouts/payout-1/execute",
        headers=_headers(subject="council-2"),
        json={"now": (now + timedelta(hours=7, minutes=59)).isoformat()},
    )

    assert early_execution.status_code == 409
    assert early_execution.json()["error"]["code"] == "payout_not_executable"

    executed = client.post(
        "/payouts/payout-1/execute",
        headers=_headers(subject="council-2"),
        json={
            "execution_id": "execution-1",
            "event_id": "evt-payout-executed-1",
            "notification_id": "notification-1",
            "now": (now + timedelta(hours=8, minutes=1)).isoformat(),
            "metadata": {"operator": "api-test"},
        },
    )

    assert executed.status_code == 200
    executed_body = executed.json()
    assert executed_body["execution_id"] == "execution-1"
    assert executed_body["payout_id"] == "payout-1"
    assert executed_body["notification_id"] == "notification-1"

    fetched = client.get("/payouts/payout-1", headers=_headers())

    assert fetched.status_code == 200
    assert fetched.json()["status"] == "executed"

    state = cast(HITLPayoutAPIState, app.state.hitl_payout_api)
    assert [message.routing_key for message in state.publisher.messages] == [
        "tenant.tenant-a.payout.queued",
        "tenant.tenant-a.payout.confirmed",
        "tenant.tenant-a.payout.executed",
    ]


def test_hitl_payout_api_veto_cancels_payout_and_filters_list() -> None:
    client = _client()
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)

    queued = client.post(
        "/payouts/queue",
        headers=_headers(subject="council-1"),
        json=_queue_payload(now=now, distribution_hash="b" * 64),
    )
    vetoed = client.post(
        "/payouts/payout-1/veto",
        headers=_headers(subject="council-2"),
        json={
            "decision_id": "veto-1",
            "event_id": "evt-payout-vetoed-1",
            "reason_code": "policy_mismatch",
            "reason": "Нужно решение Совета по новой политике выплат",
            "now": (now + timedelta(hours=2)).isoformat(),
        },
    )
    canceled = client.get(
        "/payouts",
        headers=_headers(),
        params={"status": "canceled"},
    )

    assert queued.status_code == 201
    assert vetoed.status_code == 200
    assert vetoed.json()["decision_id"] == "veto-1"
    assert canceled.status_code == 200
    assert [item["payout_id"] for item in canceled.json()["items"]] == ["payout-1"]
    assert canceled.json()["items"][0]["veto_decision_id"] == "veto-1"


def test_hitl_payout_api_rejects_late_veto_and_unauthorized_role() -> None:
    client = _client(veto_window_hours=4)
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)

    queued = client.post(
        "/payouts/queue",
        headers=_headers(subject="council-1"),
        json=_queue_payload(now=now, distribution_hash="c" * 64),
    )
    late_veto = client.post(
        "/payouts/payout-1/veto",
        headers=_headers(subject="council-2"),
        json={
            "reason_code": "policy_mismatch",
            "reason": "Окно уже закрыто",
            "now": (now + timedelta(hours=4)).isoformat(),
        },
    )
    forbidden_veto = client.post(
        "/payouts/payout-1/veto",
        headers=_headers(subject="member-1", roles=("member_full",)),
        json={
            "reason_code": "policy_mismatch",
            "reason": "Недостаточно роли",
            "now": (now + timedelta(hours=1)).isoformat(),
        },
    )

    assert queued.status_code == 201
    assert late_veto.status_code == 409
    assert late_veto.json()["error"]["code"] == "veto_window_closed"
    assert forbidden_veto.status_code == 403
    assert forbidden_veto.json()["error"]["code"] == "forbidden"


def test_hitl_payout_api_returns_403_for_tenant_override() -> None:
    app = _app()
    client = TestClient(app)
    headers = _headers()
    headers["X-Tenant-Id"] = "tenant-b"

    response = client.post(
        "/payouts/queue",
        headers=headers,
        json=_queue_payload(now=datetime(2026, 6, 18, 12, 0, tzinfo=UTC)),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "tenant_isolation_violation"
    state = cast(HITLPayoutAPIState, app.state.hitl_payout_api)
    assert state.tenant_audit_sink.events[0].event_type == "tenant.isolation_violation"
    assert state.tenant_audit_sink.events[0].tenant_id == "tenant-a"
