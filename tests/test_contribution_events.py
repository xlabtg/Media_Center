from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime

from contribution_ledger import record_contribution_event

from libs.shared import InMemoryEventBus, JSONValue


def test_contribution_event_uses_deterministic_audit_hash_and_publishes_events() -> (
    None
):
    asyncio.run(_run_contribution_event_scenario())


async def _run_contribution_event_scenario() -> None:
    bus = InMemoryEventBus()
    timestamp = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    metadata: dict[str, JSONValue] = {
        "platform": "telegram",
        "source_ref": "publication-1",
        "nested": {"b": 2, "a": 1},
    }

    result = await record_contribution_event(
        publisher=bus,
        contribution_id="contribution-1",
        tenant_id="tenant-a",
        member_id="member-1",
        contribution_type="content_creation",
        points_awarded=27.0,
        metadata=metadata,
        occurred_at=timestamp,
        event_id="evt-contribution-1",
        audit_event_id="evt-audit-1",
        correlation_id="corr-contribution-1",
    )

    expected_payload = {
        "event_type": "contribution.recorded",
        "tenant_id": "tenant-a",
        "points": 27.0,
        "metadata": metadata,
        "timestamp": "2026-06-18T12:00:00Z",
    }
    expected_hash = hashlib.sha256(
        json.dumps(expected_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    expected_member_hash = "sha256:" + hashlib.sha256(b"tenant-a:member-1").hexdigest()

    assert result.audit_hash == expected_hash
    assert result.member_id_hash == expected_member_hash

    assert [message.routing_key for message in bus.messages] == [
        "tenant.tenant-a.contribution.recorded",
        "tenant.tenant-a.audit.record.requested",
    ]

    contribution_event = bus.messages[0].envelope
    assert contribution_event.payload == {
        "contribution_id": "contribution-1",
        "member_id_hash": expected_member_hash,
        "event_type": "content_creation",
        "points_awarded": 27.0,
        "audit_hash": expected_hash,
    }

    audit_event = bus.messages[1].envelope
    assert audit_event.payload == {
        "event_type": "contribution.recorded",
        "event_id": "evt-contribution-1",
        "audit_hash": expected_hash,
        "metadata": {
            "contribution_id": "contribution-1",
            "member_id_hash": expected_member_hash,
        },
    }
