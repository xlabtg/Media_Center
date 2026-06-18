from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from libs.shared import (
    EventEnvelope,
    IdempotentEventProcessor,
    InMemoryEventBus,
    InMemoryEventIdempotencyStore,
    RabbitMQSettings,
    rabbitmq_url_from_env,
)


def _event(event_id: str = "evt-1") -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        type="contribution.recorded",
        schema_version="1.0",
        tenant_id="tenant-a",
        source="contribution-ledger",
        correlation_id="corr-events-1",
        occurred_at=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
        payload={
            "contribution_id": "contribution-1",
            "member_id_hash": "sha256:member",
            "points_awarded": 10,
            "audit_hash": "a" * 64,
        },
    )


def test_rabbitmq_url_from_env_requires_amqp_scheme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RABBITMQ_URL", raising=False)

    try:
        rabbitmq_url_from_env()
    except ValueError as error:
        assert "RABBITMQ_URL" in str(error)
    else:
        raise AssertionError("Ожидался ValueError")

    monkeypatch.setenv("RABBITMQ_URL", "redis://localhost:6379/0")
    try:
        rabbitmq_url_from_env()
    except ValueError as error:
        assert "amqp://" in str(error)
    else:
        raise AssertionError("Ожидался ValueError")

    monkeypatch.setenv("RABBITMQ_URL", "amqp://nmc:secret@localhost:5672/")
    assert rabbitmq_url_from_env() == "amqp://nmc:secret@localhost:5672/"


def test_rabbitmq_settings_use_contract_exchange_names() -> None:
    settings = RabbitMQSettings(rabbitmq_url="amqp://nmc:secret@localhost:5672/")

    assert settings.events_exchange == "nmc.events"
    assert settings.commands_exchange == "nmc.commands"
    assert settings.dead_letter_exchange == "nmc.dlx"
    assert settings.prefetch_count == 10


def test_event_envelope_serializes_and_builds_tenant_routing_key() -> None:
    event = _event()

    assert event.routing_key() == "tenant.tenant-a.contribution.recorded"

    restored = EventEnvelope.from_json(event.to_json())

    assert restored == event
    assert restored.to_dict()["occurred_at"] == "2026-06-18T12:00:00Z"


def test_in_memory_event_bus_publishes_and_consumes_by_topic_binding() -> None:
    asyncio.run(_run_event_bus_scenario())


async def _run_event_bus_scenario() -> None:
    bus = InMemoryEventBus()
    first = _event("evt-1")
    second = EventEnvelope(
        event_id="evt-2",
        type="publication.failed",
        schema_version="1.0",
        tenant_id="tenant-b",
        source="messenger-adapter",
        correlation_id="corr-events-2",
        occurred_at=datetime(2026, 6, 18, 12, 1, tzinfo=UTC),
        payload={"publication_id": "pub-1", "error_code": "rate_limited"},
    )

    await bus.publish(first)
    await bus.publish(second)

    tenant_a_events = await bus.consume("tenant.tenant-a.#")

    assert tenant_a_events == [first]
    assert await bus.consume("#") == [second]


def test_idempotent_event_processor_skips_successful_duplicate_delivery() -> None:
    asyncio.run(_run_idempotency_scenario())


async def _run_idempotency_scenario() -> None:
    event = _event()
    store = InMemoryEventIdempotencyStore()
    processor = IdempotentEventProcessor(store)
    handled_event_ids: list[str] = []

    async def handler(envelope: EventEnvelope) -> None:
        handled_event_ids.append(envelope.event_id)

    assert await processor.handle(event, handler)
    assert not await processor.handle(event, handler)
    assert handled_event_ids == ["evt-1"]
