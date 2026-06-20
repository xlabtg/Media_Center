from __future__ import annotations

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
from libs.shared.auth import TwoFactorConfirmation
from libs.shared.events import EventEnvelope, EventPublisher, InMemoryEventBus
from libs.shared.models import (
    AuditHash,
    CorrelationId,
    IdempotencyKey,
    JSONValue,
    RoleName,
    SharedBaseModel,
    SubjectId,
    TenantId,
)
from libs.shared.rbac import COUNCIL_ROLE, AccessPolicy, require_access
from libs.shared.tenant import TenantContext, assert_requested_tenant

from .audit_redaction import audit_safe_metadata

PAYOUT_CONFIRM_OPERATION = "payout.confirm"
PAYOUT_CONFIRMED_EVENT = "payout.confirmed"
PAYOUT_CONFIRM_RESOURCE_TYPE = "hitl_payout"
PAYOUT_CONFIRM_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    action=PAYOUT_CONFIRM_OPERATION,
    resource_type=PAYOUT_CONFIRM_RESOURCE_TYPE,
)


class PayoutConfirmation(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    confirmation_id: IdempotencyKey
    tenant_id: TenantId
    payout_id: IdempotencyKey
    actor_id: SubjectId
    actor_hash: str
    actor_role: RoleName
    two_factor_method: str = Field(min_length=1, max_length=32)
    operation: str = Field(default=PAYOUT_CONFIRM_OPERATION)
    audit_hash: AuditHash
    confirmed_at: datetime
    correlation_id: CorrelationId

    @field_validator("confirmed_at")
    @classmethod
    def _normalize_confirmed_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


@dataclass(slots=True)
class InMemoryPayoutConfirmationRepository:
    _confirmations: dict[tuple[str, str], PayoutConfirmation] = field(
        default_factory=dict
    )
    _payout_index: dict[tuple[str, str], str] = field(default_factory=dict)

    def add_confirmation(
        self,
        confirmation: PayoutConfirmation,
    ) -> PayoutConfirmation:
        confirmation_key = _confirmation_key(
            confirmation.tenant_id,
            confirmation.confirmation_id,
        )
        if confirmation_key in self._confirmations:
            raise PayoutQueueError(
                "Подтверждение выплаты с таким confirmation_id уже сохранено"
            )

        payout_key = _payout_key(confirmation.tenant_id, confirmation.payout_id)
        if payout_key in self._payout_index:
            raise PayoutNotExecutableError("Выплата уже подтверждена через 2FA")

        self._confirmations[confirmation_key] = confirmation
        self._payout_index[payout_key] = confirmation.confirmation_id
        return confirmation

    def get_confirmation(
        self,
        *,
        tenant_id: str,
        confirmation_id: str,
    ) -> PayoutConfirmation:
        key = _confirmation_key(tenant_id, confirmation_id)
        confirmation = self._confirmations.get(key)
        if confirmation is None:
            raise PayoutQueueError("Подтверждение выплаты не найдено в tenant")

        return confirmation

    def get_confirmation_for_payout(
        self,
        *,
        tenant_id: str,
        payout_id: str,
    ) -> PayoutConfirmation:
        payout_key = _payout_key(tenant_id, payout_id)
        confirmation_id = self._payout_index.get(payout_key)
        if confirmation_id is None:
            raise PayoutQueueError("Подтверждение выплаты не найдено в tenant")

        return self.get_confirmation(
            tenant_id=tenant_id,
            confirmation_id=confirmation_id,
        )

    def list_confirmations(self, *, tenant_id: str) -> tuple[PayoutConfirmation, ...]:
        return tuple(
            sorted(
                (
                    confirmation
                    for (record_tenant_id, _confirmation_id), confirmation in (
                        self._confirmations.items()
                    )
                    if record_tenant_id == tenant_id
                ),
                key=lambda confirmation: confirmation.confirmed_at,
            )
        )


@dataclass(slots=True)
class PayoutConfirmationManager:
    repository: InMemoryPayoutQueueRepository
    publisher: EventPublisher = field(default_factory=InMemoryEventBus)
    audit_logger: AuditLogger = field(default_factory=AuditLogger)
    confirmation_repository: InMemoryPayoutConfirmationRepository = field(
        default_factory=InMemoryPayoutConfirmationRepository
    )

    async def confirm_payout(
        self,
        *,
        tenant_id: str,
        payout_id: str,
        context: TenantContext,
        two_factor_confirmation: TwoFactorConfirmation,
        confirmation_id: str | None = None,
        event_id: str | None = None,
        metadata: Mapping[str, JSONValue] | None = None,
    ) -> PayoutConfirmation:
        assert_requested_tenant(
            tenant_id,
            context=context,
            resource_type=PAYOUT_CONFIRM_RESOURCE_TYPE,
        )
        actor_context = require_access(PAYOUT_CONFIRM_POLICY, context=context)
        if actor_context.subject is None:
            raise PayoutNotExecutableError(
                "Подтверждение выплаты требует subject в tenant context"
            )

        payout = self.repository.get_payout(tenant_id=tenant_id, payout_id=payout_id)
        if payout.status is not PayoutStatus.QUEUED:
            raise PayoutNotExecutableError(
                "Подтверждать можно только выплату в статусе queued"
            )
        if payout.confirmation_id is not None:
            raise PayoutNotExecutableError("Выплата уже подтверждена через 2FA")

        _require_matching_two_factor_confirmation(
            two_factor_confirmation,
            tenant_id=tenant_id,
            payout_id=payout_id,
            context=actor_context,
        )

        resolved_confirmation_id = confirmation_id or _new_id("confirmation")
        actor_role = _confirmed_by_role(actor_context.roles)
        actor_hash = subject_ref_hash(
            tenant_id=tenant_id,
            subject_id=actor_context.subject,
        )
        confirmed_at = _datetime_from_unix(two_factor_confirmation.confirmed_at)
        correlation_id = _resolve_correlation_id(
            context=actor_context,
            two_factor_confirmation=two_factor_confirmation,
        )
        audit_record = self.audit_logger.record(
            event_type=PAYOUT_CONFIRMED_EVENT,
            tenant_id=tenant_id,
            metadata={
                "payout_id": payout.payout_id,
                "confirmation_id": resolved_confirmation_id,
                "actor_hash": actor_hash,
                "confirmed_by_role": actor_role,
                "two_factor_method": two_factor_confirmation.method,
                "operation": two_factor_confirmation.operation,
                "resource_id": two_factor_confirmation.resource_id,
                "previous_status": payout.status.value,
                "metadata": audit_safe_metadata(metadata or {}),
            },
            timestamp=confirmed_at,
            correlation_id=correlation_id,
            actor_hash=actor_hash,
            source=HITL_PAYOUT_SOURCE,
        )
        confirmation = PayoutConfirmation(
            confirmation_id=resolved_confirmation_id,
            tenant_id=tenant_id,
            payout_id=payout_id,
            actor_id=actor_context.subject,
            actor_hash=actor_hash,
            actor_role=actor_role,
            two_factor_method=two_factor_confirmation.method,
            operation=two_factor_confirmation.operation,
            audit_hash=audit_record.audit_hash,
            confirmed_at=confirmed_at,
            correlation_id=correlation_id,
        )
        self.confirmation_repository.add_confirmation(confirmation)
        self.repository.update_payout(
            payout.with_confirmation(
                confirmation_id=confirmation.confirmation_id,
                confirmed_by=confirmation.actor_id,
                confirmed_by_hash=confirmation.actor_hash,
                confirmed_by_role=confirmation.actor_role,
                confirmed_at=confirmation.confirmed_at,
                audit_hash=confirmation.audit_hash,
            )
        )

        event = EventEnvelope(
            event_id=event_id or _new_id("evt-payout-confirmed"),
            type=PAYOUT_CONFIRMED_EVENT,
            schema_version=HITL_PAYOUT_SCHEMA_VERSION,
            tenant_id=tenant_id,
            source=HITL_PAYOUT_SOURCE,
            correlation_id=correlation_id,
            occurred_at=confirmed_at,
            payload={
                "payout_id": payout.payout_id,
                "decision_id": confirmation.confirmation_id,
                "confirmed_by_role": confirmation.actor_role,
                "audit_hash": confirmation.audit_hash,
            },
        )
        await self.publisher.publish(event)
        return confirmation

    def get_confirmation(
        self,
        *,
        tenant_id: str,
        confirmation_id: str,
    ) -> PayoutConfirmation:
        return self.confirmation_repository.get_confirmation(
            tenant_id=tenant_id,
            confirmation_id=confirmation_id,
        )

    def get_confirmation_for_payout(
        self,
        *,
        tenant_id: str,
        payout_id: str,
    ) -> PayoutConfirmation:
        return self.confirmation_repository.get_confirmation_for_payout(
            tenant_id=tenant_id,
            payout_id=payout_id,
        )

    def list_confirmations(self, *, tenant_id: str) -> tuple[PayoutConfirmation, ...]:
        return self.confirmation_repository.list_confirmations(tenant_id=tenant_id)


def _require_matching_two_factor_confirmation(
    confirmation: TwoFactorConfirmation,
    *,
    tenant_id: str,
    payout_id: str,
    context: TenantContext,
) -> None:
    if (
        confirmation.tenant_id != tenant_id
        or confirmation.subject != context.subject
        or confirmation.operation != PAYOUT_CONFIRM_OPERATION
        or confirmation.resource_id != payout_id
    ):
        raise PayoutNotExecutableError("2FA-подтверждение не соответствует выплате")
    if (
        confirmation.correlation_id is not None
        and context.correlation_id is not None
        and confirmation.correlation_id != context.correlation_id
    ):
        raise PayoutNotExecutableError(
            "2FA-подтверждение не соответствует correlation_id выплаты"
        )


def _confirmed_by_role(roles: tuple[str, ...]) -> str:
    for role in roles:
        if role in PAYOUT_CONFIRM_POLICY.allowed_roles:
            return role

    raise PayoutNotExecutableError("Подтверждающая роль не найдена в RBAC context")


def _resolve_correlation_id(
    *,
    context: TenantContext,
    two_factor_confirmation: TwoFactorConfirmation,
) -> str:
    correlation_id = context.correlation_id or two_factor_confirmation.correlation_id
    if correlation_id is None or correlation_id.strip() == "":
        raise PayoutQueueError("correlation_id обязателен для подтверждения выплаты")

    return correlation_id


def _datetime_from_unix(timestamp: int) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=UTC)


def _confirmation_key(tenant_id: str, confirmation_id: str) -> tuple[str, str]:
    return tenant_id, confirmation_id


def _payout_key(tenant_id: str, payout_id: str) -> tuple[str, str]:
    return tenant_id, payout_id


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


def _new_id(prefix: str) -> IdempotencyKey:
    return f"{prefix}-{uuid4()}"
