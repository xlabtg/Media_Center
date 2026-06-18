from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime

from contribution_ledger import (
    build_payout_distribution_export,
    calculate_weights,
    canonical_distribution_json,
    publish_payout_distribution_ready,
)

from libs.shared import InMemoryEventBus


def test_payout_exporter_builds_hitl_distribution_from_weight_result() -> None:
    weights = calculate_weights(
        {
            "avg_points_council": 100,
            "members": [
                {"member_id": "member-c", "points": 50},
                {"member_id": "member-a", "points": 5},
                {"member_id": "member-b", "points": 10},
            ],
        }
    )

    distribution = build_payout_distribution_export(
        tenant_id="tenant-a",
        period="2026-06",
        weights=weights,
        distribution_id="distribution-1",
        created_by="council-1",
        created_at=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
    )

    assert distribution.member_count == 3
    assert distribution.total_kv_capped == 0.25
    assert distribution.total_payout_share == 1.0
    assert [member.member_id for member in distribution.members] == [
        "member-a",
        "member-b",
        "member-c",
    ]
    assert [member.payout_share for member in distribution.members] == [
        0.2,
        0.4,
        0.4,
    ]

    expected_hash = hashlib.sha256(
        json.dumps(
            {
                "members": [
                    {
                        "kv_capped": 0.05,
                        "kv_raw": 0.05,
                        "member_id": "member-a",
                        "payout_share": 0.2,
                        "total_points": 5.0,
                    },
                    {
                        "kv_capped": 0.1,
                        "kv_raw": 0.1,
                        "member_id": "member-b",
                        "payout_share": 0.4,
                        "total_points": 10.0,
                    },
                    {
                        "kv_capped": 0.1,
                        "kv_raw": 0.5,
                        "member_id": "member-c",
                        "payout_share": 0.4,
                        "total_points": 50.0,
                    },
                ],
                "period": "2026-06",
                "tenant_id": "tenant-a",
                "total_kv_capped": 0.25,
                "total_payout_share": 1.0,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    assert distribution.distribution_hash == expected_hash
    assert canonical_distribution_json(distribution) == json.dumps(
        distribution.to_hash_payload(),
        sort_keys=True,
    )


def test_payout_exporter_publishes_distribution_ready_event() -> None:
    asyncio.run(_run_distribution_ready_scenario())


async def _run_distribution_ready_scenario() -> None:
    bus = InMemoryEventBus()
    distribution = build_payout_distribution_export(
        tenant_id="tenant-a",
        period="2026-06",
        weights=calculate_weights(
            avg_points_council=100,
            members=[{"member_id": "member-a", "points": 10}],
        ),
        distribution_id="distribution-1",
        created_by="council-1",
        created_at=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
    )

    event = await publish_payout_distribution_ready(
        publisher=bus,
        distribution=distribution,
        event_id="evt-distribution-ready-1",
        correlation_id="corr-distribution-1",
        occurred_at=datetime(2026, 6, 18, 12, 1, tzinfo=UTC),
    )

    assert bus.messages[0].routing_key == "tenant.tenant-a.payout.distribution_ready"
    assert bus.messages[0].envelope == event
    assert event.payload == {
        "period": "2026-06",
        "distribution_id": "distribution-1",
        "distribution_hash": distribution.distribution_hash,
        "member_count": 1,
    }
