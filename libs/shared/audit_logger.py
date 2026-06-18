from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, cast

from pydantic import Field, field_validator

from libs.shared.models import (
    AuditHash,
    CorrelationId,
    EventType,
    JSONValue,
    SharedBaseModel,
    TenantId,
)

AUDIT_HASH_ALGORITHM = "sha256"

_TENANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")

type AuditPoints = int | float | None
AuditClock = Callable[[], datetime]


class AuditPayload(SharedBaseModel):
    event_type: EventType
    tenant_id: TenantId
    points: AuditPoints = None
    metadata: dict[str, JSONValue] = Field(default_factory=dict)
    timestamp: datetime

    @field_validator("points")
    @classmethod
    def _reject_bool_points(cls, value: AuditPoints) -> AuditPoints:
        if isinstance(value, bool):
            raise ValueError("points не может быть bool")
        return value

    def to_hash_payload(self) -> dict[str, object]:
        return {
            "event_type": self.event_type,
            "tenant_id": self.tenant_id,
            "points": self.points,
            "metadata": self.metadata,
            "timestamp": _format_timestamp(self.timestamp),
        }


class AuditLogRecord(AuditPayload):
    audit_hash: AuditHash
    correlation_id: CorrelationId | None = None
    actor_hash: str | None = None
    source: str | None = None


class AuditLogSink(Protocol):
    def record(self, record: AuditLogRecord) -> None:
        """Persist or publish a hash-only audit log record."""


@dataclass(slots=True)
class InMemoryAuditLogSink:
    """In-memory sink for unit tests and local service wiring."""

    _records: list[AuditLogRecord] = field(default_factory=list)

    @property
    def records(self) -> tuple[AuditLogRecord, ...]:
        return tuple(self._records)

    def record(self, record: AuditLogRecord) -> None:
        self._records.append(record)


class AuditLogger:
    """Reusable hash generator for service audit events."""

    def __init__(
        self,
        *,
        sink: AuditLogSink | None = None,
        clock: AuditClock | None = None,
    ) -> None:
        self._sink = sink
        self._clock = clock or _utcnow

    def record(
        self,
        *,
        event_type: str,
        tenant_id: str,
        points: AuditPoints = None,
        metadata: Mapping[str, JSONValue] | None = None,
        timestamp: datetime | str | None = None,
        correlation_id: str | None = None,
        actor_hash: str | None = None,
        source: str | None = None,
    ) -> AuditLogRecord:
        resolved_timestamp = timestamp if timestamp is not None else self._clock()
        payload = build_audit_payload(
            event_type=event_type,
            tenant_id=tenant_id,
            points=points,
            metadata=metadata,
            timestamp=resolved_timestamp,
        )
        record = AuditLogRecord(
            **payload.model_dump(mode="python"),
            audit_hash=audit_hash_from_payload(payload),
            correlation_id=correlation_id,
            actor_hash=actor_hash,
            source=source,
        )
        if self._sink is not None:
            self._sink.record(record)

        return record


def audit_hash(
    *,
    event_type: str,
    tenant_id: str,
    points: AuditPoints = None,
    metadata: Mapping[str, JSONValue] | None = None,
    timestamp: datetime | str,
) -> str:
    return audit_hash_from_payload(
        build_audit_payload(
            event_type=event_type,
            tenant_id=tenant_id,
            points=points,
            metadata=metadata,
            timestamp=timestamp,
        )
    )


def audit_hash_from_payload(payload: AuditPayload) -> str:
    return hashlib.sha256(canonical_audit_json(payload).encode("utf-8")).hexdigest()


def canonical_audit_json(payload: AuditPayload) -> str:
    return json.dumps(payload.to_hash_payload(), sort_keys=True)


def build_audit_payload(
    *,
    event_type: str,
    tenant_id: str,
    points: AuditPoints = None,
    metadata: Mapping[str, JSONValue] | None = None,
    timestamp: datetime | str,
) -> AuditPayload:
    return AuditPayload(
        event_type=event_type,
        tenant_id=_normalize_tenant_id(tenant_id),
        points=points,
        metadata=_clone_metadata(metadata),
        timestamp=_normalize_timestamp(timestamp),
    )


def _clone_metadata(
    metadata: Mapping[str, JSONValue] | None,
) -> dict[str, JSONValue]:
    if metadata is None:
        return {}

    return cast(
        dict[str, JSONValue],
        json.loads(json.dumps(dict(metadata), sort_keys=True)),
    )


def _normalize_timestamp(timestamp: datetime | str) -> datetime:
    if isinstance(timestamp, str):
        if timestamp.strip() == "":
            raise ValueError("timestamp не может быть пустым")
        normalized = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    else:
        normalized = timestamp

    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=UTC)

    return normalized.astimezone(UTC)


def _format_timestamp(timestamp: datetime) -> str:
    return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _normalize_tenant_id(tenant_id: str) -> str:
    if not _TENANT_ID_PATTERN.fullmatch(tenant_id):
        raise ValueError("tenant_id имеет недопустимый формат")
    return tenant_id


def _utcnow() -> datetime:
    return datetime.now(UTC)
