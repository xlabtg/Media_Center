from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import AliasChoices, ConfigDict, Field, field_validator

from libs.shared.audit_logger import AuditLogger
from libs.shared.events import EventEnvelope, EventPublisher
from libs.shared.models import (
    AuditHash,
    CorrelationId,
    IdempotencyKey,
    JSONValue,
    SharedBaseModel,
    SubjectId,
    TenantId,
)

from .points_calculator import ContributionEventType
from .weight_engine import NonNegativeFiniteFloat

CONTRIBUTION_RECORDED_EVENT = "contribution.recorded"
AUDIT_RECORD_REQUESTED_EVENT = "audit.record.requested"
CONTRIBUTION_EVENT_SCHEMA_VERSION = "1.0"
CONTRIBUTION_EVENT_SOURCE = "contribution-ledger"


class ContributionEventInput(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    contribution_id: IdempotencyKey
    tenant_id: TenantId
    member_id: SubjectId
    contribution_type: ContributionEventType = Field(
        validation_alias=AliasChoices(
            "contribution_type",
            "event_type",
            "action",
        )
    )
    points_awarded: NonNegativeFiniteFloat = Field(
        validation_alias=AliasChoices("points_awarded", "points", "final_points")
    )
    metadata: dict[str, JSONValue] = Field(default_factory=dict)
    occurred_at: datetime
    correlation_id: CorrelationId
    event_id: IdempotencyKey | None = None
    audit_event_id: IdempotencyKey | None = None
    causation_id: IdempotencyKey | None = None

    @field_validator("contribution_type", mode="before")
    @classmethod
    def _normalize_contribution_type(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("occurred_at")
    @classmethod
    def _normalize_occurred_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


@dataclass(frozen=True, slots=True)
class ContributionEventResult:
    contribution_id: str
    tenant_id: str
    member_id_hash: str
    contribution_type: str
    points_awarded: float
    audit_hash: AuditHash
    contribution_event: EventEnvelope
    audit_event: EventEnvelope


async def record_contribution_event(
    *,
    publisher: EventPublisher,
    contribution_id: str,
    tenant_id: str,
    member_id: str,
    contribution_type: ContributionEventType | str,
    points_awarded: float,
    metadata: Mapping[str, JSONValue] | None = None,
    occurred_at: datetime | str | None = None,
    event_id: str | None = None,
    audit_event_id: str | None = None,
    correlation_id: str,
    causation_id: str | None = None,
    audit_logger: AuditLogger | None = None,
) -> ContributionEventResult:
    request = ContributionEventInput.model_validate(
        {
            "contribution_id": contribution_id,
            "tenant_id": tenant_id,
            "member_id": member_id,
            "contribution_type": contribution_type,
            "points_awarded": points_awarded,
            "metadata": dict(metadata or {}),
            "occurred_at": _normalize_datetime(occurred_at or datetime.now(UTC)),
            "event_id": event_id,
            "audit_event_id": audit_event_id,
            "correlation_id": correlation_id,
            "causation_id": causation_id,
        }
    )
    logger = audit_logger or AuditLogger()
    audit_record = logger.record(
        event_type=CONTRIBUTION_RECORDED_EVENT,
        tenant_id=request.tenant_id,
        points=request.points_awarded,
        metadata=request.metadata,
        timestamp=request.occurred_at,
        correlation_id=request.correlation_id,
        source=CONTRIBUTION_EVENT_SOURCE,
    )
    member_hash = member_id_hash(
        tenant_id=request.tenant_id,
        member_id=request.member_id,
    )
    contribution_event = _build_contribution_recorded_event(
        request=request,
        event_id=request.event_id or _new_event_id("evt-contribution"),
        member_hash=member_hash,
        audit_hash=audit_record.audit_hash,
    )
    audit_event = _build_audit_requested_event(
        request=request,
        event_id=request.audit_event_id or _new_event_id("evt-audit"),
        audited_event_id=contribution_event.event_id,
        member_hash=member_hash,
        audit_hash=audit_record.audit_hash,
    )

    await publisher.publish(contribution_event)
    await publisher.publish(audit_event)

    return ContributionEventResult(
        contribution_id=request.contribution_id,
        tenant_id=request.tenant_id,
        member_id_hash=member_hash,
        contribution_type=request.contribution_type.value,
        points_awarded=request.points_awarded,
        audit_hash=audit_record.audit_hash,
        contribution_event=contribution_event,
        audit_event=audit_event,
    )


def member_id_hash(*, tenant_id: str, member_id: str) -> str:
    return "sha256:" + hashlib.sha256(f"{tenant_id}:{member_id}".encode()).hexdigest()


def _build_contribution_recorded_event(
    *,
    request: ContributionEventInput,
    event_id: str,
    member_hash: str,
    audit_hash: AuditHash,
) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        type=CONTRIBUTION_RECORDED_EVENT,
        schema_version=CONTRIBUTION_EVENT_SCHEMA_VERSION,
        tenant_id=request.tenant_id,
        source=CONTRIBUTION_EVENT_SOURCE,
        correlation_id=request.correlation_id,
        occurred_at=request.occurred_at,
        causation_id=request.causation_id,
        payload={
            "contribution_id": request.contribution_id,
            "member_id_hash": member_hash,
            "event_type": request.contribution_type.value,
            "points_awarded": request.points_awarded,
            "audit_hash": audit_hash,
        },
    )


def _build_audit_requested_event(
    *,
    request: ContributionEventInput,
    event_id: str,
    audited_event_id: str,
    member_hash: str,
    audit_hash: AuditHash,
) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        type=AUDIT_RECORD_REQUESTED_EVENT,
        schema_version=CONTRIBUTION_EVENT_SCHEMA_VERSION,
        tenant_id=request.tenant_id,
        source=CONTRIBUTION_EVENT_SOURCE,
        correlation_id=request.correlation_id,
        occurred_at=request.occurred_at,
        causation_id=audited_event_id,
        payload={
            "event_type": CONTRIBUTION_RECORDED_EVENT,
            "event_id": audited_event_id,
            "audit_hash": audit_hash,
            "metadata": {
                "contribution_id": request.contribution_id,
                "member_id_hash": member_hash,
            },
        },
    )


def _normalize_datetime(value: datetime | str) -> datetime:
    if isinstance(value, str):
        normalized = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        normalized = value

    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=UTC)

    return normalized.astimezone(UTC)


def _new_event_id(prefix: str) -> IdempotencyKey:
    return f"{prefix}-{uuid4()}"
