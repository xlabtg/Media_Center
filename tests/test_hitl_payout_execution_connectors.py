from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from hitl_payout_gateway import (
    PAYOUT_CONFIRM_OPERATION,
    PAYOUT_EXECUTED_EVENT,
    PAYOUT_FAILED_EVENT,
    InMemoryBlockchainAuditConnector,
    InMemoryNotificationConnector,
    InMemoryPaymentConnector,
    PayoutConnectorError,
    PayoutExecutionManager,
    PayoutQueueManager,
    PayoutStatus,
)

from libs.shared import (
    AuditLogger,
    InMemoryAuditLogSink,
    InMemoryEventBus,
    TenantContext,
    TOTPService,
)

TOTP_SECRET = "JBSWY3DPEHPK3PXP"


def test_execute_payout_records_audit_hash_and_notifies_member() -> None:
    asyncio.run(_run_successful_execution_scenario())


async def _run_successful_execution_scenario() -> None:
    bus = InMemoryEventBus()
    audit_sink = InMemoryAuditLogSink()
    audit_logger = AuditLogger(sink=audit_sink)
    payment_connector = InMemoryPaymentConnector()
    blockchain_auditor = InMemoryBlockchainAuditConnector()
    notification_connector = InMemoryNotificationConnector()
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    queue_manager = await _confirmed_queue_manager(
        bus=bus,
        audit_logger=audit_logger,
        now=now,
    )
    execution_manager = PayoutExecutionManager(
        repository=queue_manager.repository,
        publisher=bus,
        audit_logger=audit_logger,
        payment_connector=payment_connector,
        blockchain_auditor=blockchain_auditor,
        notification_connector=notification_connector,
    )

    ready = queue_manager.mark_ready_for_execution(
        tenant_id="tenant-a",
        payout_id="payout-1",
        now=now + timedelta(hours=8),
    )

    receipt = await execution_manager.execute_payout(
        tenant_id="tenant-a",
        payout_id="payout-1",
        correlation_id="corr-payout-1",
        execution_id="execution-1",
        event_id="evt-payout-executed-1",
        notification_id="notification-1",
        now=now + timedelta(hours=8, minutes=1),
        metadata={"operator": "unit-test"},
    )
    executed = queue_manager.get_payout(
        tenant_id="tenant-a",
        payout_id="payout-1",
    )
    expected_ref_hash = (
        "sha256:" + hashlib.sha256(b"tenant-a:payment-execution-1").hexdigest()
    )

    assert ready.status is PayoutStatus.READY_TO_EXECUTE
    assert executed.status is PayoutStatus.EXECUTED
    assert executed.execution_id == "execution-1"
    assert executed.execution_ref_hash == expected_ref_hash
    assert executed.notification_id == "notification-1"
    assert executed.audit_chain_ref == "audit-chain-evt-payout-executed-1"
    assert receipt.audit_hash == audit_sink.records[2].audit_hash
    assert receipt.execution_ref_hash == expected_ref_hash
    assert len(payment_connector.commands) == 1
    assert payment_connector.commands[0].payout_share == 0.25
    assert blockchain_auditor.records[0].audit_hash == receipt.audit_hash
    assert "payout_share" not in blockchain_auditor.records[0].metadata
    assert "member_id" not in blockchain_auditor.records[0].metadata
    assert notification_connector.notifications[0].recipient_id == "member-1"
    assert notification_connector.notifications[0].template_key == (
        "hitl_payout_executed"
    )
    assert [record.event_type for record in audit_sink.records] == [
        "payout.queued",
        "payout.confirmed",
        PAYOUT_EXECUTED_EVENT,
    ]
    assert bus.messages[-1].routing_key == "tenant.tenant-a.payout.executed"
    assert bus.messages[-1].envelope.payload == {
        "payout_id": "payout-1",
        "execution_ref_hash": expected_ref_hash,
        "status": "executed",
        "audit_hash": receipt.audit_hash,
    }


def test_connector_failure_is_logged_and_published_without_executing_payout(
    caplog: pytest.LogCaptureFixture,
) -> None:
    asyncio.run(_run_connector_failure_scenario(caplog))


async def _run_connector_failure_scenario(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus = InMemoryEventBus()
    audit_sink = InMemoryAuditLogSink()
    audit_logger = AuditLogger(sink=audit_sink)
    failing_payment = InMemoryPaymentConnector(
        fail_with=PayoutConnectorError(
            "Платёжный шлюз временно недоступен",
            connector_name="payment_gateway",
            error_code="payment_unavailable",
            retryable=True,
        )
    )
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    queue_manager = await _confirmed_queue_manager(
        bus=bus,
        audit_logger=audit_logger,
        now=now,
    )
    queue_manager.mark_ready_for_execution(
        tenant_id="tenant-a",
        payout_id="payout-1",
        now=now + timedelta(hours=8),
    )
    execution_manager = PayoutExecutionManager(
        repository=queue_manager.repository,
        publisher=bus,
        audit_logger=audit_logger,
        payment_connector=failing_payment,
    )

    with (
        caplog.at_level("WARNING"),
        pytest.raises(PayoutConnectorError, match="Платёжный шлюз"),
    ):
        await execution_manager.execute_payout(
            tenant_id="tenant-a",
            payout_id="payout-1",
            correlation_id="corr-payout-1",
            execution_id="execution-1",
            failure_event_id="evt-payout-failed-1",
            now=now + timedelta(hours=8, minutes=1),
        )

    payout = queue_manager.get_payout(tenant_id="tenant-a", payout_id="payout-1")

    assert payout.status is PayoutStatus.READY_TO_EXECUTE
    assert payout.execution_id is None
    assert [record.event_type for record in audit_sink.records] == [
        "payout.queued",
        "payout.confirmed",
        PAYOUT_FAILED_EVENT,
    ]
    assert audit_sink.records[-1].metadata == {
        "payout_id": "payout-1",
        "connector": "payment_gateway",
        "error_code": "payment_unavailable",
        "retryable": True,
        "previous_status": "ready_to_execute",
        "metadata": {},
    }
    assert bus.messages[-1].routing_key == "tenant.tenant-a.payout.failed"
    assert bus.messages[-1].envelope.payload == {
        "payout_id": "payout-1",
        "error_code": "payment_unavailable",
        "retryable": True,
        "connector": "payment_gateway",
        "audit_hash": audit_sink.records[-1].audit_hash,
    }
    assert any(
        "Сбой коннектора исполнения выплаты" in record.message
        for record in caplog.records
    )


@pytest.mark.parametrize(
    ("failed_connector", "error_code", "connector_name"),
    [
        ("blockchain", "audit_chain_unavailable", "blockchain_auditor"),
        ("notification", "notification_unavailable", "notification_gateway"),
    ],
)
def test_downstream_connector_failure_keeps_payout_retryable(
    failed_connector: str,
    error_code: str,
    connector_name: str,
) -> None:
    asyncio.run(
        _run_downstream_connector_failure_scenario(
            failed_connector=failed_connector,
            error_code=error_code,
            connector_name=connector_name,
        )
    )


async def _run_downstream_connector_failure_scenario(
    *,
    failed_connector: str,
    error_code: str,
    connector_name: str,
) -> None:
    bus = InMemoryEventBus()
    audit_sink = InMemoryAuditLogSink()
    audit_logger = AuditLogger(sink=audit_sink)
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    queue_manager = await _confirmed_queue_manager(
        bus=bus,
        audit_logger=audit_logger,
        now=now,
    )
    queue_manager.mark_ready_for_execution(
        tenant_id="tenant-a",
        payout_id="payout-1",
        now=now + timedelta(hours=8),
    )
    connector_error = PayoutConnectorError(
        "Downstream connector is unavailable",
        connector_name=connector_name,
        error_code=error_code,
        retryable=True,
    )
    blockchain_auditor = InMemoryBlockchainAuditConnector(
        fail_with=connector_error if failed_connector == "blockchain" else None,
    )
    notification_connector = InMemoryNotificationConnector(
        fail_with=connector_error if failed_connector == "notification" else None,
    )
    execution_manager = PayoutExecutionManager(
        repository=queue_manager.repository,
        publisher=bus,
        audit_logger=audit_logger,
        blockchain_auditor=blockchain_auditor,
        notification_connector=notification_connector,
    )

    with pytest.raises(PayoutConnectorError, match="Downstream"):
        await execution_manager.execute_payout(
            tenant_id="tenant-a",
            payout_id="payout-1",
            correlation_id="corr-payout-1",
            execution_id="execution-1",
            event_id="evt-payout-executed-1",
            failure_event_id="evt-payout-failed-1",
            now=now + timedelta(hours=8, minutes=1),
        )

    payout = queue_manager.get_payout(tenant_id="tenant-a", payout_id="payout-1")

    assert payout.status is PayoutStatus.READY_TO_EXECUTE
    assert payout.execution_id is None
    assert audit_sink.records[-1].event_type == PAYOUT_FAILED_EVENT
    assert audit_sink.records[-1].metadata["connector"] == connector_name
    assert audit_sink.records[-1].metadata["error_code"] == error_code
    assert bus.messages[-1].envelope.type == PAYOUT_FAILED_EVENT
    assert bus.messages[-1].envelope.payload["connector"] == connector_name
    assert bus.messages[-1].envelope.payload["error_code"] == error_code


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
        distribution_id="distribution-1",
        distribution_hash="a" * 64,
        created_by="council-1",
        correlation_id="corr-payout-1",
        payout_id="payout-1",
        event_id="evt-payout-queued-1",
        now=now,
    )
    context = TenantContext(
        tenant_id="tenant-a",
        subject="council-2",
        roles=("council",),
        correlation_id="corr-payout-1",
    )
    two_factor = TOTPService(clock=lambda: now.timestamp() + 60)
    confirmation = two_factor.confirm_sensitive_operation(
        context=context,
        secret=TOTP_SECRET,
        code=two_factor.generate_totp(TOTP_SECRET),
        operation=PAYOUT_CONFIRM_OPERATION,
        resource_id="payout-1",
    )

    from hitl_payout_gateway import PayoutConfirmationManager

    confirmation_manager = PayoutConfirmationManager(
        repository=queue_manager.repository,
        publisher=bus,
        audit_logger=audit_logger,
    )
    await confirmation_manager.confirm_payout(
        tenant_id="tenant-a",
        payout_id="payout-1",
        context=context,
        two_factor_confirmation=confirmation,
        confirmation_id="confirmation-1",
        event_id="evt-payout-confirmed-1",
    )
    return queue_manager
