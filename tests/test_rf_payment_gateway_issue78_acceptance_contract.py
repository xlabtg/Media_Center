from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from hitl_payout_gateway import (
    PAYOUT_CONFIRM_OPERATION,
    PAYOUT_PAYMENT_STATUS_SYNCED_EVENT,
    InMemoryBlockchainAuditConnector,
    PayoutConnectorError,
    PayoutExecutionManager,
    PayoutPaymentStatus,
    PayoutQueueManager,
    RFPayoutGatewayConfig,
    RFPayoutGatewayConnector,
)
from pydantic import SecretStr

from libs.shared import (
    AuditLogger,
    InMemoryAuditLogSink,
    InMemoryEventBus,
    JSONValue,
    TenantContext,
    TOTPService,
)

ROOT = Path(__file__).resolve().parents[1]
TOTP_SECRET = "JBSWY3DPEHPK3PXP"
PAYMENT_METADATA: dict[str, JSONValue] = {
    "payment": {
        "amount_minor": 125_000,
        "currency": "RUB",
        "recipient_token": "recipient-token-78",
        "rails": "sbp",
        "purpose": "issue-78 acceptance payout",
    },
    "operator": "issue-78",
}


def test_issue_78_rf_gateway_executes_syncs_status_and_handles_refund() -> None:
    asyncio.run(_run_rf_gateway_status_scenario())


async def _run_rf_gateway_status_scenario() -> None:
    requests: list[httpx.Request] = []
    status_calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and request.url.path == "/api/payouts":
            payload = json.loads(request.content.decode("utf-8"))
            assert request.headers["Authorization"] == "Bearer rf-test-token"
            assert request.headers["Idempotency-Key"] == "execution-issue-78"
            assert request.headers["X-Correlation-Id"] == "corr-issue-78"
            assert payload == {
                "merchant_id": "merchant-78",
                "payout_id": "payout-issue-78",
                "execution_id": "execution-issue-78",
                "amount_minor": 125_000,
                "currency": "RUB",
                "recipient_token": "recipient-token-78",
                "rails": "sbp",
                "purpose": "issue-78 acceptance payout",
                "member_ref_hash": payload["member_ref_hash"],
                "distribution_hash": "f" * 64,
                "correlation_id": "corr-issue-78",
            }
            assert "member_id" not in payload
            return httpx.Response(
                202,
                json={
                    "payment_id": "rfpay-78",
                    "status": "accepted",
                    "accepted_at": "2026-06-18T20:01:00Z",
                },
            )

        if request.method == "GET" and request.url.path == "/api/payouts/rfpay-78":
            status_calls["count"] += 1
            if status_calls["count"] == 1:
                return httpx.Response(
                    200,
                    json={
                        "payment_id": "rfpay-78",
                        "status": "succeeded",
                        "synced_at": "2026-06-18T20:05:00Z",
                    },
                )

            return httpx.Response(
                200,
                json={
                    "payment_id": "rfpay-78",
                    "status": "refunded",
                    "refund_id": "refund-78",
                    "error_code": "returned_by_bank",
                    "synced_at": "2026-06-18T21:00:00Z",
                },
            )

        return httpx.Response(404, json={"error_code": "unexpected_request"})

    bus = InMemoryEventBus()
    audit_sink = InMemoryAuditLogSink()
    audit_logger = AuditLogger(sink=audit_sink)
    blockchain_auditor = InMemoryBlockchainAuditConnector()
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    queue_manager = await _confirmed_queue_manager(
        bus=bus,
        audit_logger=audit_logger,
        now=now,
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = RFPayoutGatewayConnector(
            config=RFPayoutGatewayConfig(
                base_url="https://rf-pay.example/api",
                merchant_id="merchant-78",
                api_key=SecretStr("rf-test-token"),
            ),
            client=client,
        )
        execution_manager = PayoutExecutionManager(
            repository=queue_manager.repository,
            publisher=bus,
            audit_logger=audit_logger,
            payment_connector=connector,
            blockchain_auditor=blockchain_auditor,
        )

        receipt = await execution_manager.execute_payout(
            tenant_id="tenant-a",
            payout_id="payout-issue-78",
            correlation_id="corr-issue-78",
            execution_id="execution-issue-78",
            event_id="evt-issue-78-executed",
            notification_id="notification-issue-78",
            now=now + timedelta(hours=8, minutes=1),
            metadata=PAYMENT_METADATA,
        )
        succeeded = await execution_manager.sync_payment_status(
            tenant_id="tenant-a",
            payout_id="payout-issue-78",
            correlation_id="corr-issue-78",
            event_id="evt-issue-78-status-succeeded",
            now=now + timedelta(hours=8, minutes=5),
            metadata={"source": "acceptance-test"},
        )
        refunded = await execution_manager.sync_payment_status(
            tenant_id="tenant-a",
            payout_id="payout-issue-78",
            correlation_id="corr-issue-78",
            event_id="evt-issue-78-status-refunded",
            now=now + timedelta(hours=9),
            metadata={"source": "acceptance-test"},
        )

    payout = queue_manager.get_payout(
        tenant_id="tenant-a",
        payout_id="payout-issue-78",
    )

    assert receipt.execution_id == "execution-issue-78"
    assert payout.payment_connector_name == "rf_payment_gateway"
    assert payout.payment_gateway_id == "rfpay-78"
    assert succeeded.status is PayoutPaymentStatus.SUCCEEDED
    assert refunded.status is PayoutPaymentStatus.REFUNDED
    assert refunded.refund_id == "refund-78"
    assert payout.payment_status is PayoutPaymentStatus.REFUNDED
    assert payout.payment_error_code == "returned_by_bank"
    assert payout.payment_refund_id == "refund-78"
    assert [request.url.path for request in requests] == [
        "/api/payouts",
        "/api/payouts/rfpay-78",
        "/api/payouts/rfpay-78",
    ]
    assert [message.envelope.type for message in bus.messages] == [
        "payout.queued",
        "payout.confirmed",
        "payout.executed",
        PAYOUT_PAYMENT_STATUS_SYNCED_EVENT,
        PAYOUT_PAYMENT_STATUS_SYNCED_EVENT,
    ]
    assert blockchain_auditor.records[0].metadata == {
        "payout_id": "payout-issue-78",
        "execution_id": "execution-issue-78",
        "execution_ref_hash": receipt.execution_ref_hash,
        "source": "hitl-payout-gateway",
    }
    _assert_sensitive_payment_data_is_not_published(
        audit_records=[record.model_dump_json() for record in audit_sink.records],
        event_payloads=[message.envelope.to_json() for message in bus.messages],
    )


def test_issue_78_rf_gateway_errors_are_audited_without_sensitive_metadata() -> None:
    asyncio.run(_run_rf_gateway_error_scenario())


def test_issue_78_rf_gateway_provider_names_connector_by_default() -> None:
    connector = RFPayoutGatewayConnector(
        config=RFPayoutGatewayConfig(
            provider="rf_bank_gateway",
            base_url="https://rf-pay.example/api",
            merchant_id="merchant-78",
            api_key=SecretStr("rf-test-token"),
        )
    )

    assert connector.connector_name == "rf_bank_gateway"


async def _run_rf_gateway_error_scenario() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(
            503,
            json={
                "error_code": "bank_unavailable",
                "message": "Bank processing is temporarily unavailable",
            },
        )

    bus = InMemoryEventBus()
    audit_sink = InMemoryAuditLogSink()
    audit_logger = AuditLogger(sink=audit_sink)
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    queue_manager = await _confirmed_queue_manager(
        bus=bus,
        audit_logger=audit_logger,
        now=now,
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = RFPayoutGatewayConnector(
            config=RFPayoutGatewayConfig(
                base_url="https://rf-pay.example/api",
                merchant_id="merchant-78",
                api_key=SecretStr("rf-test-token"),
            ),
            client=client,
        )
        execution_manager = PayoutExecutionManager(
            repository=queue_manager.repository,
            publisher=bus,
            audit_logger=audit_logger,
            payment_connector=connector,
        )

        with pytest.raises(PayoutConnectorError) as exc_info:
            await execution_manager.execute_payout(
                tenant_id="tenant-a",
                payout_id="payout-issue-78",
                correlation_id="corr-issue-78",
                execution_id="execution-issue-78",
                failure_event_id="evt-issue-78-failed",
                now=now + timedelta(hours=8, minutes=1),
                metadata=PAYMENT_METADATA,
            )

    assert exc_info.value.connector_name == "rf_payment_gateway"
    assert exc_info.value.error_code == "bank_unavailable"
    assert exc_info.value.retryable is True
    assert audit_sink.records[-1].event_type == "payout.failed"
    assert audit_sink.records[-1].metadata["connector"] == "rf_payment_gateway"
    assert audit_sink.records[-1].metadata["error_code"] == "bank_unavailable"
    assert bus.messages[-1].envelope.payload["error_code"] == "bank_unavailable"
    _assert_sensitive_payment_data_is_not_published(
        audit_records=[record.model_dump_json() for record in audit_sink.records],
        event_payloads=[message.envelope.to_json() for message in bus.messages],
    )


def test_issue_78_docs_describe_rf_payment_gateway_contract() -> None:
    spec = (ROOT / "docs/modules/hitl-payout-gateway.md").read_text(encoding="utf-8")
    readme = (ROOT / "services/hitl-payout-gateway/README.md").read_text(
        encoding="utf-8"
    )
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    for marker in (
        "#78",
        "RFPayoutGatewayConnector",
        "payout.payment_status_synced",
        "accepted",
        "succeeded",
        "refunded",
    ):
        assert marker in spec

    for marker in (
        "RFPayoutGatewayConnector",
        "/payouts/{payout_id}/sync-status",
        'metadata["payment"]',
    ):
        assert marker in readme

    for marker in (
        "RF_PAYMENT_GATEWAY_ENABLED=",
        "RF_PAYMENT_GATEWAY_BASE_URL=",
        "RF_PAYMENT_GATEWAY_API_KEY=",
    ):
        assert marker in env_example


async def _confirmed_queue_manager(
    *,
    bus: InMemoryEventBus,
    audit_logger: AuditLogger,
    now: datetime,
) -> PayoutQueueManager:
    queue_manager = PayoutQueueManager(
        publisher=bus,
        audit_logger=audit_logger,
    )
    await queue_manager.queue_payout(
        tenant_id="tenant-a",
        member_id="member-1",
        period="2026-06",
        payout_share=0.25,
        distribution_id="distribution-issue-78",
        distribution_hash="f" * 64,
        created_by="council-1",
        correlation_id="corr-issue-78",
        payout_id="payout-issue-78",
        event_id="evt-issue-78-queued",
        now=now,
    )
    context = TenantContext(
        tenant_id="tenant-a",
        subject="council-2",
        roles=("council",),
        correlation_id="corr-issue-78",
    )
    two_factor = TOTPService(clock=lambda: now.timestamp() + 60)
    confirmation = two_factor.confirm_sensitive_operation(
        context=context,
        secret=TOTP_SECRET,
        code=two_factor.generate_totp(TOTP_SECRET),
        operation=PAYOUT_CONFIRM_OPERATION,
        resource_id="payout-issue-78",
    )

    from hitl_payout_gateway import PayoutConfirmationManager

    confirmation_manager = PayoutConfirmationManager(
        repository=queue_manager.repository,
        publisher=bus,
        audit_logger=audit_logger,
    )
    await confirmation_manager.confirm_payout(
        tenant_id="tenant-a",
        payout_id="payout-issue-78",
        context=context,
        two_factor_confirmation=confirmation,
        confirmation_id="confirmation-issue-78",
        event_id="evt-issue-78-confirmed",
    )
    return queue_manager


def _assert_sensitive_payment_data_is_not_published(
    *,
    audit_records: list[str],
    event_payloads: list[str],
) -> None:
    serialized = "\n".join(audit_records + event_payloads)
    for forbidden in (
        "125000",
        "recipient-token-78",
        "rf-test-token",
        "amount_minor",
        "recipient_token",
    ):
        assert forbidden not in serialized
