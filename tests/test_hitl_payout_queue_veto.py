from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from hitl_payout_gateway import (
    DEFAULT_VETO_WINDOW_HOURS,
    PAYOUT_CONFIRM_OPERATION,
    PAYOUT_CONFIRMED_EVENT,
    PayoutConfirmationManager,
    PayoutNotExecutableError,
    PayoutQueueManager,
    PayoutStatus,
    VetoManager,
    VetoWindowClosedError,
    resolve_veto_window_hours,
)

from libs.shared import (
    AuditLogger,
    ForbiddenError,
    InMemoryAuditLogSink,
    InMemoryEventBus,
    TenantContext,
    TOTPService,
)

TOTP_SECRET = "JBSWY3DPEHPK3PXP"


def test_queue_uses_default_veto_window_and_blocks_early_execution() -> None:
    asyncio.run(_run_queue_blocks_early_execution_scenario())


async def _run_queue_blocks_early_execution_scenario() -> None:
    bus = InMemoryEventBus()
    audit_sink = InMemoryAuditLogSink()
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    manager = PayoutQueueManager(
        publisher=bus,
        audit_logger=AuditLogger(sink=audit_sink),
    )

    result = await manager.queue_payout(
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

    payout = result.payout

    assert payout.status is PayoutStatus.QUEUED
    assert payout.veto_until == now + timedelta(hours=DEFAULT_VETO_WINDOW_HOURS)
    assert not manager.is_executable(payout, at=now + timedelta(hours=7, minutes=59))

    with pytest.raises(PayoutNotExecutableError):
        manager.mark_ready_for_execution(
            tenant_id="tenant-a",
            payout_id="payout-1",
            now=now + timedelta(hours=7, minutes=59),
        )

    with pytest.raises(PayoutNotExecutableError, match="2FA"):
        manager.mark_ready_for_execution(
            tenant_id="tenant-a",
            payout_id="payout-1",
            now=now + timedelta(hours=8),
        )

    assert not manager.is_executable(payout, at=now + timedelta(hours=8))
    assert [message.routing_key for message in bus.messages] == [
        "tenant.tenant-a.payout.queued"
    ]
    assert bus.messages[0].envelope.payload == {
        "payout_id": "payout-1",
        "period": "2026-06",
        "veto_until": "2026-06-18T20:00:00Z",
        "requires_2fa": True,
    }
    assert audit_sink.records[0].event_type == "payout.queued"
    assert audit_sink.records[0].audit_hash == payout.audit_hash


def test_council_2fa_confirmation_enables_payout_execution_after_veto_window() -> None:
    asyncio.run(_run_confirmed_payout_execution_scenario())


async def _run_confirmed_payout_execution_scenario() -> None:
    bus = InMemoryEventBus()
    audit_sink = InMemoryAuditLogSink()
    audit_logger = AuditLogger(sink=audit_sink)
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    queue_manager = PayoutQueueManager(
        publisher=bus,
        audit_logger=audit_logger,
    )
    confirmation_manager = PayoutConfirmationManager(
        repository=queue_manager.repository,
        publisher=bus,
        audit_logger=audit_logger,
    )

    await queue_manager.queue_payout(
        tenant_id="tenant-a",
        member_id="member-1",
        period="2026-06",
        payout_share=0.25,
        distribution_id="distribution-1",
        distribution_hash="b" * 64,
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

    decision = await confirmation_manager.confirm_payout(
        tenant_id="tenant-a",
        payout_id="payout-1",
        context=context,
        two_factor_confirmation=confirmation,
        confirmation_id="confirmation-1",
        event_id="evt-payout-confirmed-1",
        metadata={"approval_source": "unit-test"},
    )
    confirmed = queue_manager.get_payout(
        tenant_id="tenant-a",
        payout_id="payout-1",
    )
    expected_actor_hash = "sha256:" + hashlib.sha256(b"tenant-a:council-2").hexdigest()

    assert confirmed.confirmation_id == "confirmation-1"
    assert confirmed.confirmed_by == "council-2"
    assert confirmed.confirmed_by_hash == expected_actor_hash
    assert confirmed.confirmed_by_role == "council"
    assert confirmed.confirmed_at == datetime.fromtimestamp(
        confirmation.confirmed_at,
        tz=UTC,
    )
    assert decision.audit_hash == audit_sink.records[1].audit_hash
    assert [record.event_type for record in audit_sink.records] == [
        "payout.queued",
        PAYOUT_CONFIRMED_EVENT,
    ]
    assert bus.messages[1].routing_key == "tenant.tenant-a.payout.confirmed"
    assert bus.messages[1].envelope.payload == {
        "payout_id": "payout-1",
        "decision_id": "confirmation-1",
        "confirmed_by_role": "council",
        "audit_hash": decision.audit_hash,
    }

    with pytest.raises(PayoutNotExecutableError):
        queue_manager.mark_ready_for_execution(
            tenant_id="tenant-a",
            payout_id="payout-1",
            now=now + timedelta(hours=7, minutes=59),
        )

    ready = queue_manager.mark_ready_for_execution(
        tenant_id="tenant-a",
        payout_id="payout-1",
        now=now + timedelta(hours=8),
    )

    assert ready.status is PayoutStatus.READY_TO_EXECUTE
    assert queue_manager.is_executable(ready, at=now + timedelta(hours=8))


def test_payout_confirmation_requires_authorized_role_and_matching_2fa() -> None:
    asyncio.run(_run_confirmation_authorization_scenario())


async def _run_confirmation_authorization_scenario() -> None:
    bus = InMemoryEventBus()
    audit_logger = AuditLogger(sink=InMemoryAuditLogSink())
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    queue_manager = PayoutQueueManager(
        publisher=bus,
        audit_logger=audit_logger,
    )
    confirmation_manager = PayoutConfirmationManager(
        repository=queue_manager.repository,
        publisher=bus,
        audit_logger=audit_logger,
    )

    await queue_manager.queue_payout(
        tenant_id="tenant-a",
        member_id="member-1",
        period="2026-06",
        payout_share=0.25,
        distribution_id="distribution-1",
        distribution_hash="c" * 64,
        created_by="council-1",
        correlation_id="corr-payout-1",
        payout_id="payout-1",
        now=now,
    )

    member_context = TenantContext(
        tenant_id="tenant-a",
        subject="member-1",
        roles=("member_full",),
        correlation_id="corr-payout-1",
    )
    two_factor = TOTPService(clock=lambda: now.timestamp())
    member_confirmation = two_factor.confirm_sensitive_operation(
        context=member_context,
        secret=TOTP_SECRET,
        code=two_factor.generate_totp(TOTP_SECRET),
        operation=PAYOUT_CONFIRM_OPERATION,
        resource_id="payout-1",
    )

    with pytest.raises(ForbiddenError):
        await confirmation_manager.confirm_payout(
            tenant_id="tenant-a",
            payout_id="payout-1",
            context=member_context,
            two_factor_confirmation=member_confirmation,
        )

    council_context = TenantContext(
        tenant_id="tenant-a",
        subject="council-2",
        roles=("council",),
        correlation_id="corr-payout-1",
    )
    wrong_resource_confirmation = two_factor.confirm_sensitive_operation(
        context=council_context,
        secret=TOTP_SECRET,
        code=two_factor.generate_totp(TOTP_SECRET),
        operation=PAYOUT_CONFIRM_OPERATION,
        resource_id="payout-other",
    )

    with pytest.raises(PayoutNotExecutableError, match="2FA"):
        await confirmation_manager.confirm_payout(
            tenant_id="tenant-a",
            payout_id="payout-1",
            context=council_context,
            two_factor_confirmation=wrong_resource_confirmation,
        )


def test_veto_cancels_payout_with_decision_audit_and_event() -> None:
    asyncio.run(_run_veto_cancels_payout_scenario())


async def _run_veto_cancels_payout_scenario() -> None:
    bus = InMemoryEventBus()
    audit_sink = InMemoryAuditLogSink()
    audit_logger = AuditLogger(sink=audit_sink)
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    queue_manager = PayoutQueueManager(
        publisher=bus,
        audit_logger=audit_logger,
    )
    veto_manager = VetoManager(
        repository=queue_manager.repository,
        publisher=bus,
        audit_logger=audit_logger,
    )

    await queue_manager.queue_payout(
        tenant_id="tenant-a",
        member_id="member-1",
        period="2026-06",
        payout_share=0.25,
        distribution_id="distribution-1",
        distribution_hash="b" * 64,
        created_by="council-1",
        correlation_id="corr-payout-1",
        payout_id="payout-1",
        event_id="evt-payout-queued-1",
        now=now,
    )

    veto = await veto_manager.veto_payout(
        tenant_id="tenant-a",
        payout_id="payout-1",
        actor_id="council-2",
        reason_code="policy_mismatch",
        reason="Нужно решение Совета по новой политике выплат",
        correlation_id="corr-payout-1",
        decision_id="veto-1",
        event_id="evt-payout-vetoed-1",
        now=now + timedelta(hours=2),
    )

    canceled = queue_manager.get_payout(
        tenant_id="tenant-a",
        payout_id="payout-1",
    )
    expected_actor_hash = "sha256:" + hashlib.sha256(b"tenant-a:council-2").hexdigest()

    assert canceled.status is PayoutStatus.CANCELED
    assert canceled.veto_decision_id == "veto-1"
    assert (
        veto_manager.get_decision(
            tenant_id="tenant-a",
            decision_id="veto-1",
        )
        == veto
    )
    assert veto.audit_hash == audit_sink.records[1].audit_hash
    assert veto.actor_hash == expected_actor_hash
    assert [record.event_type for record in audit_sink.records] == [
        "payout.queued",
        "payout.vetoed",
    ]
    assert bus.messages[1].routing_key == "tenant.tenant-a.payout.vetoed"
    assert bus.messages[1].envelope.payload == {
        "payout_id": "payout-1",
        "decision_id": "veto-1",
        "reason_code": "policy_mismatch",
        "audit_hash": veto.audit_hash,
    }

    with pytest.raises(PayoutNotExecutableError):
        queue_manager.mark_ready_for_execution(
            tenant_id="tenant-a",
            payout_id="payout-1",
            now=now + timedelta(hours=8),
        )


def test_veto_after_window_is_rejected_and_window_hours_are_configurable() -> None:
    asyncio.run(_run_closed_veto_window_scenario())


async def _run_closed_veto_window_scenario() -> None:
    bus = InMemoryEventBus()
    audit_logger = AuditLogger(sink=InMemoryAuditLogSink())
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    queue_manager = PayoutQueueManager(
        publisher=bus,
        audit_logger=audit_logger,
        veto_window_hours=4,
    )
    veto_manager = VetoManager(
        repository=queue_manager.repository,
        publisher=bus,
        audit_logger=audit_logger,
    )

    queued = await queue_manager.queue_payout(
        tenant_id="tenant-a",
        member_id="member-1",
        period="2026-06",
        payout_share=0.25,
        distribution_id="distribution-1",
        distribution_hash="c" * 64,
        created_by="council-1",
        correlation_id="corr-payout-1",
        payout_id="payout-1",
        now=now,
    )

    assert queued.payout.veto_until == now + timedelta(hours=4)
    assert resolve_veto_window_hours({"VETO_WINDOW_HOURS": "12"}) == 12

    with pytest.raises(VetoWindowClosedError):
        await veto_manager.veto_payout(
            tenant_id="tenant-a",
            payout_id="payout-1",
            actor_id="council-2",
            reason_code="policy_mismatch",
            reason="Окно уже закрыто",
            correlation_id="corr-payout-1",
            now=now + timedelta(hours=4),
        )
