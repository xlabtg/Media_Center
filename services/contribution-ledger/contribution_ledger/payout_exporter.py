from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ConfigDict, Field, field_validator

from libs.shared.events import EventEnvelope, EventPublisher
from libs.shared.models import (
    AuditHash,
    IdempotencyKey,
    JSONValue,
    SharedBaseModel,
    SubjectId,
    TenantId,
)

from .weight_engine import (
    KV_CAPPED_DECIMALS,
    PAYOUT_SHARE_DECIMALS,
    MemberWeightOutput,
    NonNegativeFiniteFloat,
    WeightCalculationOutput,
)

PAYOUT_DISTRIBUTION_READY_EVENT = "payout.distribution_ready"
PAYOUT_EXPORT_SCHEMA_VERSION = "1.0"
PAYOUT_EXPORT_SOURCE = "contribution-ledger"
PAYOUT_DISTRIBUTION_STATUS_READY = "ready"

_PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
_STATUS_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"


class PayoutDistributionMember(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    member_id: SubjectId
    total_points: NonNegativeFiniteFloat
    kv_raw: NonNegativeFiniteFloat
    kv_capped: NonNegativeFiniteFloat
    payout_share: NonNegativeFiniteFloat

    def to_hash_payload(self) -> dict[str, JSONValue]:
        return {
            "member_id": self.member_id,
            "total_points": self.total_points,
            "kv_raw": self.kv_raw,
            "kv_capped": self.kv_capped,
            "payout_share": self.payout_share,
        }


class PayoutDistributionExport(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    distribution_id: IdempotencyKey
    tenant_id: TenantId
    period: str = Field(pattern=_PERIOD_PATTERN)
    status: str = Field(
        default=PAYOUT_DISTRIBUTION_STATUS_READY, pattern=_STATUS_PATTERN
    )
    total_kv_capped: NonNegativeFiniteFloat
    total_payout_share: NonNegativeFiniteFloat
    member_count: int = Field(ge=0)
    members: tuple[PayoutDistributionMember, ...]
    distribution_hash: AuditHash
    created_by: SubjectId
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)

    def to_hash_payload(self) -> dict[str, JSONValue]:
        return {
            "tenant_id": self.tenant_id,
            "period": self.period,
            "total_kv_capped": self.total_kv_capped,
            "total_payout_share": self.total_payout_share,
            "members": [member.to_hash_payload() for member in self.members],
        }

    def to_hitl_payload(self) -> dict[str, JSONValue]:
        return {
            "distribution_id": self.distribution_id,
            "tenant_id": self.tenant_id,
            "period": self.period,
            "status": self.status,
            "total_kv_capped": self.total_kv_capped,
            "total_payout_share": self.total_payout_share,
            "member_count": self.member_count,
            "members": [member.to_hash_payload() for member in self.members],
            "distribution_hash": self.distribution_hash,
            "created_by": self.created_by,
            "created_at": _format_datetime(self.created_at),
        }


PayoutDistributionMemberInput = (
    PayoutDistributionMember | MemberWeightOutput | Mapping[str, object]
)


def build_payout_distribution_export(
    *,
    tenant_id: str,
    period: str,
    weights: WeightCalculationOutput | Sequence[PayoutDistributionMemberInput],
    distribution_id: str | None = None,
    created_by: str,
    created_at: datetime | str | None = None,
) -> PayoutDistributionExport:
    members = _distribution_members_from_weights(weights)
    total_kv_capped = round(
        sum(member.kv_capped for member in members),
        KV_CAPPED_DECIMALS,
    )
    total_payout_share = round(
        sum(member.payout_share for member in members),
        PAYOUT_SHARE_DECIMALS,
    )
    hash_payload: dict[str, JSONValue] = {
        "tenant_id": tenant_id,
        "period": period,
        "total_kv_capped": total_kv_capped,
        "total_payout_share": total_payout_share,
        "members": [member.to_hash_payload() for member in members],
    }

    return PayoutDistributionExport(
        distribution_id=distribution_id or _new_event_id("distribution"),
        tenant_id=tenant_id,
        period=period,
        total_kv_capped=total_kv_capped,
        total_payout_share=total_payout_share,
        member_count=len(members),
        members=members,
        distribution_hash=_hash_json_payload(hash_payload),
        created_by=created_by,
        created_at=_normalize_datetime(created_at or datetime.now(UTC)),
    )


def canonical_distribution_json(distribution: PayoutDistributionExport) -> str:
    return json.dumps(distribution.to_hash_payload(), sort_keys=True)


async def publish_payout_distribution_ready(
    *,
    publisher: EventPublisher,
    distribution: PayoutDistributionExport,
    event_id: str | None = None,
    correlation_id: str,
    occurred_at: datetime | str | None = None,
    causation_id: str | None = None,
) -> EventEnvelope:
    event = EventEnvelope(
        event_id=event_id or _new_event_id("evt-distribution-ready"),
        type=PAYOUT_DISTRIBUTION_READY_EVENT,
        schema_version=PAYOUT_EXPORT_SCHEMA_VERSION,
        tenant_id=distribution.tenant_id,
        source=PAYOUT_EXPORT_SOURCE,
        correlation_id=correlation_id,
        occurred_at=_normalize_datetime(occurred_at or datetime.now(UTC)),
        causation_id=causation_id,
        payload={
            "period": distribution.period,
            "distribution_id": distribution.distribution_id,
            "distribution_hash": distribution.distribution_hash,
            "member_count": distribution.member_count,
        },
    )
    await publisher.publish(event)
    return event


def _distribution_members_from_weights(
    weights: WeightCalculationOutput | Sequence[PayoutDistributionMemberInput],
) -> tuple[PayoutDistributionMember, ...]:
    raw_members = (
        weights.members if isinstance(weights, WeightCalculationOutput) else weights
    )
    members = tuple(_distribution_member_from_input(member) for member in raw_members)
    return tuple(sorted(members, key=lambda member: member.member_id))


def _distribution_member_from_input(
    member: PayoutDistributionMemberInput,
) -> PayoutDistributionMember:
    if isinstance(member, PayoutDistributionMember):
        return member
    if isinstance(member, MemberWeightOutput):
        return PayoutDistributionMember(
            member_id=member.member_id,
            total_points=member.total_points,
            kv_raw=member.kv_raw,
            kv_capped=member.kv_capped,
            payout_share=member.payout_share,
        )
    return PayoutDistributionMember.model_validate(member)


def _hash_json_payload(payload: Mapping[str, JSONValue]) -> AuditHash:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _normalize_datetime(value: datetime | str) -> datetime:
    if isinstance(value, str):
        normalized = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        normalized = value

    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=UTC)

    return normalized.astimezone(UTC)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _new_event_id(prefix: str) -> IdempotencyKey:
    return f"{prefix}-{uuid4()}"
