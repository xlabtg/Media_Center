from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
from contribution_ledger import (
    ContributionLedgerAPIState,
    create_contribution_ledger_app,
)
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

TENANT_ID = "tenant-a"
LEDGER_JWT_SECRET = "issue-88-ledger-secret"
HITL_JWT_SECRET = "issue-88-hitl-secret"
TOTP_SECRET = "JBSWY3DPEHPK3PXP"
PERIOD = "2026-06"


def test_issue_88_hitl_payout_full_e2e_flow_with_negative_paths() -> None:
    ledger_app = _ledger_app()
    ledger_client = TestClient(ledger_app)
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)

    _record_contribution(
        ledger_client,
        subject="member-alpha",
        idempotency_key="contribution-issue-88-alpha",
        payload={
            "member_id": "member-alpha",
            "event_type": "content_creation",
            "source_type": "publication",
            "source_ref": "issue-88-publication-alpha",
            "platform": "telegram",
            "reach": 100_000,
            "occurred_at": _iso(now),
            "metadata": {"issue": "88"},
        },
    )
    _record_contribution(
        ledger_client,
        subject="member-beta",
        idempotency_key="contribution-issue-88-beta",
        payload={
            "member_id": "member-beta",
            "event_type": "publish",
            "source_type": "publication",
            "source_ref": "issue-88-publication-beta",
            "platform": "vk",
            "occurred_at": _iso(now + timedelta(minutes=5)),
            "metadata": {"issue": "88"},
        },
    )
    recalculated = ledger_client.post(
        "/weights/recalculate",
        headers=_headers(
            jwt_secret=LEDGER_JWT_SECRET,
            subject="council-1",
            correlation_id="corr-issue-88-ledger",
            idempotency_key="weights-issue-88",
        ),
        json={"period": PERIOD, "avg_points_council": 100},
    )
    distribution = ledger_client.get(
        "/payout-distribution",
        headers=_headers(
            jwt_secret=LEDGER_JWT_SECRET,
            subject="council-1",
            correlation_id="corr-issue-88-ledger",
        ),
        params={"period": PERIOD},
    )

    assert recalculated.status_code == 200
    assert recalculated.json()["total_kv_capped"] == 0.16
    assert [member["payout_share"] for member in recalculated.json()["members"]] == [
        0.625,
        0.375,
    ]
    assert distribution.status_code == 200
    distribution_body = cast(dict[str, object], distribution.json())
    distribution_members = cast(list[dict[str, object]], distribution_body["members"])
    assert [member["member_id"] for member in distribution_members] == [
        "member-alpha",
        "member-beta",
    ]

    hitl_app = _hitl_app()
    hitl_client = TestClient(hitl_app)
    queued_for_execution = hitl_client.post(
        "/payouts/queue",
        headers=_headers(
            jwt_secret=HITL_JWT_SECRET,
            subject="council-1",
            correlation_id="corr-issue-88-hitl",
        ),
        json=_queue_payload(
            distribution=distribution_body,
            member=distribution_members[0],
            payout_id="payout-issue-88-execute",
            event_id="evt-issue-88-execute-queued",
            now=now,
        ),
    )
    queued_for_veto = hitl_client.post(
        "/payouts/queue",
        headers=_headers(
            jwt_secret=HITL_JWT_SECRET,
            subject="council-1",
            correlation_id="corr-issue-88-hitl",
        ),
        json=_queue_payload(
            distribution=distribution_body,
            member=distribution_members[1],
            payout_id="payout-issue-88-veto",
            event_id="evt-issue-88-veto-queued",
            now=now,
        ),
    )
    execute_without_2fa = hitl_client.post(
        "/payouts/payout-issue-88-execute/execute",
        headers=_headers(
            jwt_secret=HITL_JWT_SECRET,
            subject="council-2",
            correlation_id="corr-issue-88-hitl",
        ),
        json={"now": _iso(now + timedelta(hours=8, minutes=1))},
    )
    forbidden_confirmation = hitl_client.post(
        "/payouts/payout-issue-88-execute/confirm",
        headers=_headers(
            jwt_secret=HITL_JWT_SECRET,
            subject="member-alpha",
            roles=("member_full",),
            correlation_id="corr-issue-88-hitl",
        ),
        json={
            "totp_code": "123456",
            "confirmed_at": int((now + timedelta(minutes=1)).timestamp()),
        },
    )
    vetoed = hitl_client.post(
        "/payouts/payout-issue-88-veto/veto",
        headers=_headers(
            jwt_secret=HITL_JWT_SECRET,
            subject="council-3",
            correlation_id="corr-issue-88-hitl",
        ),
        json={
            "decision_id": "veto-issue-88",
            "event_id": "evt-issue-88-vetoed",
            "reason_code": "manual_review",
            "reason": "Совет остановил выплату в рамках e2e-проверки",
            "now": _iso(now + timedelta(hours=2)),
            "metadata": {"issue": "88", "safe_note": "veto-e2e"},
        },
    )
    execute_vetoed = hitl_client.post(
        "/payouts/payout-issue-88-veto/execute",
        headers=_headers(
            jwt_secret=HITL_JWT_SECRET,
            subject="council-2",
            correlation_id="corr-issue-88-hitl",
        ),
        json={"now": _iso(now + timedelta(hours=8, minutes=1))},
    )
    confirmation_at = now + timedelta(minutes=1)
    confirmed = hitl_client.post(
        "/payouts/payout-issue-88-execute/confirm",
        headers=_headers(
            jwt_secret=HITL_JWT_SECRET,
            subject="council-2",
            correlation_id="corr-issue-88-hitl",
        ),
        json={
            "confirmation_id": "confirmation-issue-88",
            "event_id": "evt-issue-88-confirmed",
            "totp_code": _totp_code(confirmation_at),
            "confirmed_at": int(confirmation_at.timestamp()),
            "metadata": {"approval_source": "issue-88-e2e"},
        },
    )
    early_execution = hitl_client.post(
        "/payouts/payout-issue-88-execute/execute",
        headers=_headers(
            jwt_secret=HITL_JWT_SECRET,
            subject="council-2",
            correlation_id="corr-issue-88-hitl",
        ),
        json={"now": _iso(now + timedelta(hours=7, minutes=59))},
    )
    late_veto = hitl_client.post(
        "/payouts/payout-issue-88-execute/veto",
        headers=_headers(
            jwt_secret=HITL_JWT_SECRET,
            subject="council-3",
            correlation_id="corr-issue-88-hitl",
        ),
        json={
            "reason_code": "manual_review",
            "reason": "Окно вето уже закрыто",
            "now": _iso(now + timedelta(hours=8)),
        },
    )
    executed = hitl_client.post(
        "/payouts/payout-issue-88-execute/execute",
        headers=_headers(
            jwt_secret=HITL_JWT_SECRET,
            subject="council-2",
            correlation_id="corr-issue-88-hitl",
        ),
        json={
            "execution_id": "execution-issue-88",
            "event_id": "evt-issue-88-executed",
            "notification_id": "notification-issue-88",
            "now": _iso(now + timedelta(hours=8, minutes=1)),
            "metadata": {
                "payment": {
                    "amount_minor": 125_000,
                    "currency": "RUB",
                    "recipient_token": "recipient-token-issue-88",
                    "rails": "sbp",
                },
                "operator_token": "operator-secret-issue-88",
                "safe_note": "execution-e2e",
            },
        },
    )

    assert queued_for_execution.status_code == 201
    assert queued_for_execution.json()["status"] == "queued"
    assert queued_for_execution.json()["payout_share"] == 0.625
    assert queued_for_veto.status_code == 201
    assert queued_for_veto.json()["payout_share"] == 0.375
    _assert_error(execute_without_2fa, status_code=409, code="payout_not_executable")
    _assert_error(forbidden_confirmation, status_code=403, code="forbidden")
    assert vetoed.status_code == 200
    assert vetoed.json()["decision_id"] == "veto-issue-88"
    _assert_error(execute_vetoed, status_code=409, code="payout_not_executable")
    assert confirmed.status_code == 200
    assert confirmed.json()["actor_id"] == "council-2"
    assert confirmed.json()["actor_role"] == "council"
    _assert_error(early_execution, status_code=409, code="payout_not_executable")
    _assert_error(late_veto, status_code=409, code="veto_window_closed")
    assert executed.status_code == 200
    executed_body = executed.json()
    assert executed_body["execution_id"] == "execution-issue-88"
    assert executed_body["audit_chain_ref"] == "audit-chain-evt-issue-88-executed"
    assert executed_body["notification_id"] == "notification-issue-88"

    executed_payout = hitl_client.get(
        "/payouts/payout-issue-88-execute",
        headers=_headers(jwt_secret=HITL_JWT_SECRET, subject="council-1"),
    )
    canceled_payouts = hitl_client.get(
        "/payouts",
        headers=_headers(jwt_secret=HITL_JWT_SECRET, subject="council-1"),
        params={"status": "canceled"},
    )

    assert executed_payout.status_code == 200
    assert executed_payout.json()["status"] == "executed"
    assert canceled_payouts.status_code == 200
    assert [item["payout_id"] for item in canceled_payouts.json()["items"]] == [
        "payout-issue-88-veto"
    ]

    ledger_state = cast(
        ContributionLedgerAPIState,
        ledger_app.state.contribution_ledger_api,
    )
    hitl_state = cast(HITLPayoutAPIState, hitl_app.state.hitl_payout_api)
    payment_connector = cast(
        InMemoryPaymentConnector,
        hitl_state.execution_manager.payment_connector,
    )
    blockchain_auditor = cast(
        InMemoryBlockchainAuditConnector,
        hitl_state.execution_manager.blockchain_auditor,
    )
    notification_connector = cast(
        InMemoryNotificationConnector,
        hitl_state.execution_manager.notification_connector,
    )

    assert [message.envelope.type for message in ledger_state.publisher.messages] == [
        "contribution.recorded",
        "audit.record.requested",
        "contribution.recorded",
        "audit.record.requested",
    ]
    assert [record.event_type for record in hitl_state.audit_log_sink.records] == [
        "payout.queued",
        "payout.queued",
        "payout.vetoed",
        "payout.confirmed",
        "payout.executed",
    ]
    assert [message.envelope.type for message in hitl_state.publisher.messages] == [
        "payout.queued",
        "payout.queued",
        "payout.vetoed",
        "payout.confirmed",
        "payout.executed",
    ]
    assert len(payment_connector.commands) == 1
    payment_command = payment_connector.commands[0]
    assert payment_command.payout_id == "payout-issue-88-execute"
    assert payment_command.payout_share == 0.625
    assert payment_command.distribution_hash == distribution_body["distribution_hash"]
    assert len(blockchain_auditor.records) == 1
    assert blockchain_auditor.records[0].audit_hash == executed_body["audit_hash"]
    assert blockchain_auditor.records[0].metadata == {
        "payout_id": "payout-issue-88-execute",
        "execution_id": "execution-issue-88",
        "execution_ref_hash": executed_body["execution_ref_hash"],
        "source": "hitl-payout-gateway",
    }
    assert len(notification_connector.notifications) == 1
    assert notification_connector.notifications[0].recipient_id == "member-alpha"
    assert notification_connector.notifications[0].template_key == (
        "hitl_payout_executed"
    )
    assert notification_connector.notifications[0].metadata == {
        "payout_id": "payout-issue-88-execute",
        "execution_ref_hash": executed_body["execution_ref_hash"],
        "audit_hash": executed_body["audit_hash"],
        "audit_chain_ref": executed_body["audit_chain_ref"],
    }

    public_audit_payload = "".join(
        record.model_dump_json() for record in hitl_state.audit_log_sink.records
    )
    public_event_payload = "".join(
        message.envelope.to_json() for message in hitl_state.publisher.messages
    )
    public_blockchain_payload = "".join(
        record.model_dump_json() for record in blockchain_auditor.records
    )
    public_payload = (
        f"{public_audit_payload}{public_event_payload}{public_blockchain_payload}"
    )
    assert "execution-e2e" in public_payload
    assert "RUB" in public_payload
    assert "sbp" in public_payload
    for leaked_value in (
        "125000",
        "recipient-token-issue-88",
        "operator-secret-issue-88",
        "member-alpha",
        "member-beta",
    ):
        assert leaked_value not in public_payload


def _ledger_app() -> FastAPI:
    return create_contribution_ledger_app(
        ServiceTemplateConfig(
            service_name="contribution-ledger",
            version="0.1.0",
            jwt_secret=LEDGER_JWT_SECRET,
            prometheus_enabled=True,
        )
    )


def _hitl_app() -> FastAPI:
    return create_hitl_payout_app(
        ServiceTemplateConfig(
            service_name="hitl-payout-gateway",
            version="0.1.0",
            jwt_secret=HITL_JWT_SECRET,
            prometheus_enabled=True,
        ),
        veto_window_hours=8,
        totp_secrets={(TENANT_ID, "council-2"): TOTP_SECRET},
    )


def _record_contribution(
    client: TestClient,
    *,
    subject: str,
    idempotency_key: str,
    payload: dict[str, object],
) -> None:
    response = client.post(
        "/contributions",
        headers=_headers(
            jwt_secret=LEDGER_JWT_SECRET,
            subject=subject,
            roles=("member_full",),
            correlation_id="corr-issue-88-ledger",
            idempotency_key=idempotency_key,
        ),
        json=payload,
    )

    assert response.status_code == 201


def _queue_payload(
    *,
    distribution: dict[str, object],
    member: dict[str, object],
    payout_id: str,
    event_id: str,
    now: datetime,
) -> dict[str, object]:
    return {
        "payout_id": payout_id,
        "event_id": event_id,
        "member_id": member["member_id"],
        "period": distribution["period"],
        "payout_share": member["payout_share"],
        "distribution_id": distribution["distribution_id"],
        "distribution_hash": distribution["distribution_hash"],
        "created_by": "council-1",
        "now": _iso(now),
        "metadata": {"issue": "88", "source": "e2e-contract"},
    }


def _headers(
    *,
    jwt_secret: str,
    subject: str,
    roles: tuple[str, ...] = ("council",),
    correlation_id: str = "corr-issue-88",
    idempotency_key: str | None = None,
    tenant_id: str = TENANT_ID,
) -> dict[str, str]:
    token = encode_hs256_jwt(
        {
            "tenant_id": tenant_id,
            "sub": subject,
            "roles": list(roles),
        },
        jwt_secret,
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": tenant_id,
        "X-Correlation-Id": correlation_id,
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _totp_code(at: datetime) -> str:
    totp = TOTPService(clock=lambda: at.timestamp())
    return totp.generate_totp(TOTP_SECRET)


def _assert_error(response: httpx.Response, *, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
