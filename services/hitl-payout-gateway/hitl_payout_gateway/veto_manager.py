from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ConfigDict, Field, field_validator

from hitl_payout_gateway.queue_manager import (
    HITL_PAYOUT_SCHEMA_VERSION,
    HITL_PAYOUT_SOURCE,
    InMemoryPayoutQueueRepository,
    PayoutNotExecutableError,
    PayoutQueueError,
    PayoutStatus,
    subject_ref_hash,
)
from libs.shared.audit_logger import AuditLogger
from libs.shared.events import EventEnvelope, EventPublisher, InMemoryEventBus
from libs.shared.models import (
    AuditHash,
    CorrelationId,
    IdempotencyKey,
    JSONValue,
    SharedBaseModel,
    SubjectId,
    TenantId,
)

PAYOUT_VETOED_EVENT = "payout.vetoed"

_REASON_CODE_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"


class VetoWindowClosedError(PayoutNotExecutableError):
    """Raised when the Council tries to veto after the configured window."""


class VetoDecision(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    decision_id: IdempotencyKey
    tenant_id: TenantId
    payout_id: IdempotencyKey
    actor_id: SubjectId
    actor_hash: str
    reason_code: str = Field(pattern=_REASON_CODE_PATTERN)
    reason: str = Field(min_length=1, max_length=512)
    reason_hash: str
    audit_hash: AuditHash
    decided_at: datetime
    correlation_id: CorrelationId

    @field_validator("decided_at")
    @classmethod
    def _normalize_decided_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


@dataclass(slots=True)
class InMemoryVetoDecisionRepository:
    _decisions: dict[tuple[str, str], VetoDecision] = field(default_factory=dict)

    def add_decision(self, decision: VetoDecision) -> VetoDecision:
        key = _decision_key(decision.tenant_id, decision.decision_id)
        if key in self._decisions:
            raise PayoutQueueError("Решение вето с таким decision_id уже сохранено")

        self._decisions[key] = decision
        return decision

    def get_decision(self, *, tenant_id: str, decision_id: str) -> VetoDecision:
        key = _decision_key(tenant_id, decision_id)
        decision = self._decisions.get(key)
        if decision is None:
            raise PayoutQueueError("Решение вето не найдено в tenant")
        return decision

    def list_decisions(self, *, tenant_id: str) -> tuple[VetoDecision, ...]:
        return tuple(
            sorted(
                (
                    decision
                    for (record_tenant_id, _decision_id), decision in (
                        self._decisions.items()
                    )
                    if record_tenant_id == tenant_id
                ),
                key=lambda decision: decision.decided_at,
            )
        )


@dataclass(slots=True)
class VetoManager:
    repository: InMemoryPayoutQueueRepository
    publisher: EventPublisher = field(default_factory=InMemoryEventBus)
    audit_logger: AuditLogger = field(default_factory=AuditLogger)
    decision_repository: InMemoryVetoDecisionRepository = field(
        default_factory=InMemoryVetoDecisionRepository
    )

    async def veto_payout(
        self,
        *,
        tenant_id: str,
        payout_id: str,
        actor_id: str,
        reason_code: str,
        reason: str,
        correlation_id: str,
        decision_id: str | None = None,
        event_id: str | None = None,
        now: datetime | str | None = None,
        metadata: Mapping[str, JSONValue] | None = None,
    ) -> VetoDecision:
        decided_at = _normalize_datetime(now or datetime.now(UTC))
        payout = self.repository.get_payout(tenant_id=tenant_id, payout_id=payout_id)
        if payout.status is not PayoutStatus.QUEUED:
            raise PayoutNotExecutableError(
                "Вето можно наложить только на выплату в статусе queued"
            )
        if decided_at >= payout.veto_until:
            raise VetoWindowClosedError("Окно вето для выплаты уже закрыто")

        resolved_decision_id = decision_id or _new_id("veto")
        actor_hash = subject_ref_hash(tenant_id=tenant_id, subject_id=actor_id)
        reason_hash = _reason_hash(tenant_id=tenant_id, reason=reason)
        audit_record = self.audit_logger.record(
            event_type=PAYOUT_VETOED_EVENT,
            tenant_id=tenant_id,
            metadata={
                "payout_id": payout.payout_id,
                "decision_id": resolved_decision_id,
                "actor_hash": actor_hash,
                "reason_code": reason_code,
                "reason_hash": reason_hash,
                "previous_status": payout.status.value,
                "new_status": PayoutStatus.CANCELED.value,
                "metadata": dict(metadata or {}),
            },
            timestamp=decided_at,
            correlation_id=correlation_id,
            actor_hash=actor_hash,
            source=HITL_PAYOUT_SOURCE,
        )
        decision = VetoDecision(
            decision_id=resolved_decision_id,
            tenant_id=tenant_id,
            payout_id=payout_id,
            actor_id=actor_id,
            actor_hash=actor_hash,
            reason_code=reason_code,
            reason=reason,
            reason_hash=reason_hash,
            audit_hash=audit_record.audit_hash,
            decided_at=decided_at,
            correlation_id=correlation_id,
        )
        self.decision_repository.add_decision(decision)
        self.repository.update_payout(
            payout.with_status(
                status=PayoutStatus.CANCELED,
                updated_at=decided_at,
                audit_hash=decision.audit_hash,
                veto_decision_id=decision.decision_id,
            )
        )

        event = EventEnvelope(
            event_id=event_id or _new_id("evt-payout-vetoed"),
            type=PAYOUT_VETOED_EVENT,
            schema_version=HITL_PAYOUT_SCHEMA_VERSION,
            tenant_id=tenant_id,
            source=HITL_PAYOUT_SOURCE,
            correlation_id=correlation_id,
            occurred_at=decided_at,
            payload={
                "payout_id": payout.payout_id,
                "decision_id": decision.decision_id,
                "reason_code": decision.reason_code,
                "audit_hash": decision.audit_hash,
            },
        )
        await self.publisher.publish(event)
        return decision

    def get_decision(self, *, tenant_id: str, decision_id: str) -> VetoDecision:
        return self.decision_repository.get_decision(
            tenant_id=tenant_id,
            decision_id=decision_id,
        )

    def list_decisions(self, *, tenant_id: str) -> tuple[VetoDecision, ...]:
        return self.decision_repository.list_decisions(tenant_id=tenant_id)


def _reason_hash(*, tenant_id: str, reason: str) -> str:
    return "sha256:" + hashlib.sha256(f"{tenant_id}:{reason}".encode()).hexdigest()


def _decision_key(tenant_id: str, decision_id: str) -> tuple[str, str]:
    return tenant_id, decision_id


def _normalize_datetime(value: datetime | str) -> datetime:
    if isinstance(value, str):
        normalized = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        normalized = value

    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=UTC)

    return normalized.astimezone(UTC)


def _new_id(prefix: str) -> IdempotencyKey:
    return f"{prefix}-{uuid4()}"
