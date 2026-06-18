from __future__ import annotations

import json
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import import_module
from typing import Any, Protocol, cast
from urllib.parse import urlparse

RABBITMQ_URL_ENV = "RABBITMQ_URL"
RABBITMQ_URL_SCHEMES = frozenset({"amqp", "amqps"})
DEFAULT_EVENTS_EXCHANGE = "nmc.events"
DEFAULT_COMMANDS_EXCHANGE = "nmc.commands"
DEFAULT_DEAD_LETTER_EXCHANGE = "nmc.dlx"

type JSONValue = (
    None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
)
EventHandler = Callable[["EventEnvelope"], Awaitable[None]]

_EVENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_SCHEMA_VERSION_PATTERN = re.compile(r"^\d+\.\d+$")


class EventPublisher(Protocol):
    async def publish(self, envelope: EventEnvelope) -> None:
        """Publish an event envelope to the configured event transport."""


@dataclass(frozen=True, slots=True)
class RabbitMQSettings:
    rabbitmq_url: str
    events_exchange: str = DEFAULT_EVENTS_EXCHANGE
    commands_exchange: str = DEFAULT_COMMANDS_EXCHANGE
    dead_letter_exchange: str = DEFAULT_DEAD_LETTER_EXCHANGE
    prefetch_count: int = 10

    def __post_init__(self) -> None:
        if self.prefetch_count <= 0:
            raise ValueError("prefetch_count должен быть положительным")

        object.__setattr__(
            self,
            "rabbitmq_url",
            validate_rabbitmq_url(self.rabbitmq_url),
        )
        object.__setattr__(
            self,
            "events_exchange",
            _normalize_name(self.events_exchange, "events_exchange"),
        )
        object.__setattr__(
            self,
            "commands_exchange",
            _normalize_name(self.commands_exchange, "commands_exchange"),
        )
        object.__setattr__(
            self,
            "dead_letter_exchange",
            _normalize_name(self.dead_letter_exchange, "dead_letter_exchange"),
        )

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        env_var: str = RABBITMQ_URL_ENV,
        events_exchange: str = DEFAULT_EVENTS_EXCHANGE,
        commands_exchange: str = DEFAULT_COMMANDS_EXCHANGE,
        dead_letter_exchange: str = DEFAULT_DEAD_LETTER_EXCHANGE,
        prefetch_count: int = 10,
    ) -> RabbitMQSettings:
        return cls(
            rabbitmq_url=rabbitmq_url_from_env(environ, env_var=env_var),
            events_exchange=events_exchange,
            commands_exchange=commands_exchange,
            dead_letter_exchange=dead_letter_exchange,
            prefetch_count=prefetch_count,
        )


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_id: str
    type: str
    schema_version: str
    tenant_id: str
    source: str
    correlation_id: str
    occurred_at: datetime
    payload: dict[str, JSONValue] = field(default_factory=dict)
    causation_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "event_id",
            _normalize_token(self.event_id, "event_id"),
        )
        object.__setattr__(
            self,
            "type",
            _normalize_event_type(self.type),
        )
        object.__setattr__(
            self,
            "schema_version",
            _normalize_schema_version(self.schema_version),
        )
        object.__setattr__(
            self,
            "tenant_id",
            _normalize_routing_segment(self.tenant_id, "tenant_id"),
        )
        object.__setattr__(
            self,
            "source",
            _normalize_token(self.source, "source"),
        )
        object.__setattr__(
            self,
            "correlation_id",
            _normalize_token(self.correlation_id, "correlation_id"),
        )
        object.__setattr__(
            self,
            "occurred_at",
            _normalize_datetime(self.occurred_at),
        )
        object.__setattr__(self, "payload", _clone_json_object(self.payload))
        if self.causation_id is not None:
            object.__setattr__(
                self,
                "causation_id",
                _normalize_token(self.causation_id, "causation_id"),
            )

    def routing_key(self) -> str:
        return event_routing_key(self.tenant_id, self.type)

    def to_dict(self) -> dict[str, JSONValue]:
        data: dict[str, JSONValue] = {
            "event_id": self.event_id,
            "type": self.type,
            "schema_version": self.schema_version,
            "tenant_id": self.tenant_id,
            "source": self.source,
            "correlation_id": self.correlation_id,
            "occurred_at": _format_datetime(self.occurred_at),
            "payload": _clone_json_object(self.payload),
        }
        if self.causation_id is not None:
            data["causation_id"] = self.causation_id

        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> EventEnvelope:
        payload = data.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("payload должен быть JSON object")

        return cls(
            event_id=_required_string(data, "event_id"),
            type=_required_string(data, "type"),
            schema_version=_required_string(data, "schema_version"),
            tenant_id=_required_string(data, "tenant_id"),
            source=_required_string(data, "source"),
            correlation_id=_required_string(data, "correlation_id"),
            occurred_at=_parse_datetime(_required_string(data, "occurred_at")),
            payload=cast(dict[str, JSONValue], payload),
            causation_id=_optional_string(data, "causation_id"),
        )

    @classmethod
    def from_json(cls, raw_json: str | bytes) -> EventEnvelope:
        loaded = json.loads(raw_json)
        if not isinstance(loaded, dict):
            raise ValueError("event envelope должен быть JSON object")

        return cls.from_dict(cast(dict[str, object], loaded))


@dataclass(frozen=True, slots=True)
class PublishedEvent:
    envelope: EventEnvelope
    routing_key: str


class InMemoryEventBus:
    """In-memory topic bus for unit tests and local service wiring."""

    def __init__(self) -> None:
        self._messages: list[PublishedEvent] = []

    @property
    def messages(self) -> tuple[PublishedEvent, ...]:
        return tuple(self._messages)

    async def publish(self, envelope: EventEnvelope) -> None:
        self._messages.append(
            PublishedEvent(
                envelope=envelope,
                routing_key=envelope.routing_key(),
            )
        )

    async def consume(
        self,
        binding_key: str = "#",
        *,
        limit: int | None = None,
    ) -> list[EventEnvelope]:
        if limit is not None and limit <= 0:
            raise ValueError("limit должен быть положительным")

        consumed: list[EventEnvelope] = []
        remaining: list[PublishedEvent] = []
        for message in self._messages:
            can_consume_more = limit is None or len(consumed) < limit
            if can_consume_more and topic_matches(binding_key, message.routing_key):
                consumed.append(message.envelope)
            else:
                remaining.append(message)

        self._messages = remaining
        return consumed


class EventIdempotencyStore(Protocol):
    async def begin(self, event_id: str) -> bool:
        """Return False when this event was already completed or is in progress."""

    async def complete(self, event_id: str) -> None:
        """Mark an event as successfully processed."""

    async def fail(self, event_id: str) -> None:
        """Release an event for retry after a handler failure."""


class InMemoryEventIdempotencyStore:
    """In-memory inbox state for deterministic idempotency tests."""

    def __init__(self) -> None:
        self._in_progress: set[str] = set()
        self._completed: set[str] = set()

    async def begin(self, event_id: str) -> bool:
        normalized_event_id = _normalize_token(event_id, "event_id")
        if (
            normalized_event_id in self._completed
            or normalized_event_id in self._in_progress
        ):
            return False

        self._in_progress.add(normalized_event_id)
        return True

    async def complete(self, event_id: str) -> None:
        normalized_event_id = _normalize_token(event_id, "event_id")
        self._in_progress.discard(normalized_event_id)
        self._completed.add(normalized_event_id)

    async def fail(self, event_id: str) -> None:
        self._in_progress.discard(_normalize_token(event_id, "event_id"))


@dataclass(frozen=True, slots=True)
class IdempotentEventProcessor:
    idempotency_store: EventIdempotencyStore

    async def handle(
        self,
        envelope: EventEnvelope,
        handler: EventHandler,
    ) -> bool:
        started = await self.idempotency_store.begin(envelope.event_id)
        if not started:
            return False

        try:
            await handler(envelope)
        except Exception:
            await self.idempotency_store.fail(envelope.event_id)
            raise

        await self.idempotency_store.complete(envelope.event_id)
        return True


@dataclass(slots=True)
class RabbitMQEventBus:
    """RabbitMQ publisher/topology adapter for the shared event contract."""

    connection: Any
    channel: Any
    events_exchange: Any
    settings: RabbitMQSettings

    @classmethod
    async def connect(cls, settings: RabbitMQSettings) -> RabbitMQEventBus:
        aio_pika = cast(Any, import_module("aio_pika"))

        connection = await aio_pika.connect_robust(settings.rabbitmq_url)
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=settings.prefetch_count)
        events_exchange = await channel.declare_exchange(
            settings.events_exchange,
            aio_pika.ExchangeType.TOPIC,
            durable=True,
        )
        await channel.declare_exchange(
            settings.commands_exchange,
            aio_pika.ExchangeType.TOPIC,
            durable=True,
        )
        await channel.declare_exchange(
            settings.dead_letter_exchange,
            aio_pika.ExchangeType.TOPIC,
            durable=True,
        )

        return cls(
            connection=connection,
            channel=channel,
            events_exchange=events_exchange,
            settings=settings,
        )

    async def publish(self, envelope: EventEnvelope) -> None:
        aio_pika = cast(Any, import_module("aio_pika"))
        message = aio_pika.Message(
            body=envelope.to_json().encode("utf-8"),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            correlation_id=envelope.correlation_id,
            message_id=envelope.event_id,
            headers={
                "tenant_id": envelope.tenant_id,
                "event_type": envelope.type,
                "schema_version": envelope.schema_version,
                "source": envelope.source,
            },
        )
        await self.events_exchange.publish(message, routing_key=envelope.routing_key())

    async def declare_queue(self, queue_name: str, binding_key: str) -> Any:
        queue = await self.channel.declare_queue(
            _normalize_name(queue_name, "queue_name"),
            durable=True,
            arguments={"x-dead-letter-exchange": self.settings.dead_letter_exchange},
        )
        await queue.bind(
            self.events_exchange,
            routing_key=_normalize_binding_key(binding_key),
        )

        return queue

    async def close(self) -> None:
        await self.connection.close()


def rabbitmq_url_from_env(
    environ: Mapping[str, str] | None = None,
    *,
    env_var: str = RABBITMQ_URL_ENV,
) -> str:
    source = os.environ if environ is None else environ
    rabbitmq_url = source.get(env_var)
    if rabbitmq_url is None or rabbitmq_url.strip() == "":
        raise ValueError(f"{env_var} должен быть задан")

    return validate_rabbitmq_url(rabbitmq_url)


def validate_rabbitmq_url(rabbitmq_url: str) -> str:
    normalized_url = rabbitmq_url.strip()
    if normalized_url == "":
        raise ValueError("RABBITMQ_URL должен быть непустой строкой")

    parsed_url = urlparse(normalized_url)
    if parsed_url.scheme not in RABBITMQ_URL_SCHEMES or parsed_url.netloc == "":
        raise ValueError("RABBITMQ_URL должен использовать amqp:// или amqps://")

    return normalized_url


def event_routing_key(tenant_id: str, event_type: str) -> str:
    normalized_tenant_id = _normalize_routing_segment(tenant_id, "tenant_id")
    normalized_event_type = _normalize_event_type(event_type)

    return f"tenant.{normalized_tenant_id}.{normalized_event_type}"


def topic_matches(binding_key: str, routing_key: str) -> bool:
    binding_parts = tuple(_normalize_binding_key(binding_key).split("."))
    routing_parts = tuple(_normalize_binding_key(routing_key).split("."))

    return _topic_parts_match(binding_parts, routing_parts)


def _topic_parts_match(
    binding_parts: tuple[str, ...],
    routing_parts: tuple[str, ...],
) -> bool:
    if not binding_parts:
        return not routing_parts

    head = binding_parts[0]
    tail = binding_parts[1:]
    if head == "#":
        if not tail:
            return True

        return any(
            _topic_parts_match(tail, routing_parts[index:])
            for index in range(len(routing_parts) + 1)
        )

    if not routing_parts:
        return False

    if head == "*" or head == routing_parts[0]:
        return _topic_parts_match(tail, routing_parts[1:])

    return False


def _normalize_event_type(event_type: str) -> str:
    normalized = event_type.strip()
    if not _EVENT_TYPE_PATTERN.fullmatch(normalized):
        raise ValueError(
            "type должен быть в формате domain.event без пробелов и верхнего регистра"
        )

    return normalized


def _normalize_schema_version(schema_version: str) -> str:
    normalized = schema_version.strip()
    if not _SCHEMA_VERSION_PATTERN.fullmatch(normalized):
        raise ValueError("schema_version должен быть в формате major.minor")

    return normalized


def _normalize_routing_segment(value: str, label: str) -> str:
    normalized = _normalize_token(value, label)
    if "." in normalized:
        raise ValueError(f"{label} не должен содержать '.' для RabbitMQ routing key")

    return normalized


def _normalize_token(value: str, label: str) -> str:
    normalized = value.strip()
    if normalized == "":
        raise ValueError(f"{label} должен быть непустой строкой")
    if any(character.isspace() for character in normalized):
        raise ValueError(f"{label} не должен содержать пробелы")

    return normalized


def _normalize_name(value: str, label: str) -> str:
    normalized = _normalize_token(value, label)
    if ":" in normalized:
        raise ValueError(f"{label} не должен содержать ':'")

    return normalized


def _normalize_binding_key(binding_key: str) -> str:
    normalized = binding_key.strip()
    if normalized == "":
        raise ValueError("binding_key должен быть непустой строкой")
    if any(character.isspace() for character in normalized):
        raise ValueError("binding_key не должен содержать пробелы")

    return normalized


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("occurred_at должен содержать timezone")

    return value.astimezone(UTC)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_datetime(raw_value: str) -> datetime:
    return _normalize_datetime(datetime.fromisoformat(raw_value.replace("Z", "+00:00")))


def _clone_json_object(value: Mapping[str, JSONValue]) -> dict[str, JSONValue]:
    cloned = json.loads(json.dumps(value, ensure_ascii=False))
    if not isinstance(cloned, dict):
        raise ValueError("payload должен быть JSON object")

    return cast(dict[str, JSONValue], cloned)


def _required_string(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} должен быть строкой")

    return value


def _optional_string(data: Mapping[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} должен быть строкой")

    return value
