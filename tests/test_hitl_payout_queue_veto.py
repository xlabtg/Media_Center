from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from hitl_payout_gateway import (
    DEFAULT_VETO_WINDOW_HOURS,
    PayoutNotExecutableError,
    PayoutQueueManager,
    PayoutStatus,
    VetoManager,
    VetoWindowClosedError,
    resolve_veto_window_hours,
)

from libs.shared import AuditLogger, InMemoryAuditLogSink, InMemoryEventBus


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

    ready = manager.mark_ready_for_execution(
        tenant_id="tenant-a",
        payout_id="payout-1",
        now=now + timedelta(hours=8),
    )

    assert ready.status is PayoutStatus.READY_TO_EXECUTE
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
