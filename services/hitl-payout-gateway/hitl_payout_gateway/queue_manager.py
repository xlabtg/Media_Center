from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import uuid4

from pydantic import ConfigDict, Field, field_validator

from libs.shared.audit_logger import AuditLogger
from libs.shared.events import EventEnvelope, EventPublisher, InMemoryEventBus
from libs.shared.models import (
    AuditHash,
    IdempotencyKey,
    JSONValue,
    RoleName,
    SharedBaseModel,
    SubjectId,
    TenantId,
)

VETO_WINDOW_HOURS_ENV = "VETO_WINDOW_HOURS"
DEFAULT_VETO_WINDOW_HOURS = 8
MIN_VETO_WINDOW_HOURS = 4
MAX_VETO_WINDOW_HOURS = 12

HITL_PAYOUT_SOURCE = "hitl-payout-gateway"
HITL_PAYOUT_SCHEMA_VERSION = "1.0"
PAYOUT_QUEUED_EVENT = "payout.queued"

_PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


class PayoutQueueError(RuntimeError):
    """Base error for queue-manager domain rule violations."""


class PayoutNotFoundError(PayoutQueueError):
    """Raised when a tenant payout id is unknown."""


class PayoutNotExecutableError(PayoutQueueError):
    """Raised when the payout cannot move toward execution yet."""


class PayoutStatus(StrEnum):
    QUEUED = "queued"
    READY_TO_EXECUTE = "ready_to_execute"
    CANCELED = "canceled"
    EXECUTED = "executed"


class PayoutPaymentStatus(StrEnum):
    ACCEPTED = "accepted"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETURNED = "returned"
    REFUNDED = "refunded"


class PayoutQueueItem(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    payout_id: IdempotencyKey
    tenant_id: TenantId
    member_id: SubjectId
    member_hash: str
    period: str = Field(pattern=_PERIOD_PATTERN)
    payout_share: float = Field(ge=0, le=1, allow_inf_nan=False)
    distribution_id: IdempotencyKey
    distribution_hash: AuditHash
    status: PayoutStatus
    veto_until: datetime
    requires_2fa: bool = True
    confirmation_id: IdempotencyKey | None = None
    confirmed_by: SubjectId | None = None
    confirmed_by_hash: str | None = None
    confirmed_by_role: RoleName | None = None
    confirmed_at: datetime | None = None
    audit_hash: AuditHash
    created_by: SubjectId
    created_by_hash: str
    created_at: datetime
    updated_at: datetime
    veto_decision_id: IdempotencyKey | None = None
    execution_id: IdempotencyKey | None = None
    execution_ref_hash: str | None = None
    audit_chain_ref: str | None = None
    notification_id: IdempotencyKey | None = None
    executed_at: datetime | None = None
    payment_connector_name: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_-]{0,63}$",
    )
    payment_gateway_id: str | None = Field(default=None, min_length=1, max_length=256)
    payment_status: PayoutPaymentStatus | None = None
    payment_status_synced_at: datetime | None = None
    payment_error_code: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_.-]{0,127}$",
    )
    payment_refund_id: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("veto_until", "created_at", "updated_at")
    @classmethod
    def _normalize_datetime_field(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)

    @field_validator("confirmed_at", "executed_at", "payment_status_synced_at")
    @classmethod
    def _normalize_optional_datetime_field(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is None:
            return None

        return _normalize_datetime(value)

    def with_status(
        self,
        *,
        status: PayoutStatus,
        updated_at: datetime,
        audit_hash: str | None = None,
        veto_decision_id: str | None = None,
    ) -> PayoutQueueItem:
        updates: dict[str, object] = {
            "status": status,
            "updated_at": _normalize_datetime(updated_at),
        }
        if audit_hash is not None:
            updates["audit_hash"] = audit_hash
        if veto_decision_id is not None:
            updates["veto_decision_id"] = veto_decision_id
        return self.model_copy(update=updates)

    def with_confirmation(
        self,
        *,
        confirmation_id: str,
        confirmed_by: str,
        confirmed_by_hash: str,
        confirmed_by_role: str,
        confirmed_at: datetime,
        audit_hash: str,
    ) -> PayoutQueueItem:
        return self.model_copy(
            update={
                "confirmation_id": confirmation_id,
                "confirmed_by": confirmed_by,
                "confirmed_by_hash": confirmed_by_hash,
                "confirmed_by_role": confirmed_by_role,
                "confirmed_at": _normalize_datetime(confirmed_at),
                "audit_hash": audit_hash,
                "updated_at": _normalize_datetime(confirmed_at),
            }
        )

    def with_execution(
        self,
        *,
        execution_id: str,
        execution_ref_hash: str,
        audit_chain_ref: str,
        notification_id: str,
        executed_at: datetime,
        audit_hash: str,
        payment_connector_name: str | None = None,
        payment_gateway_id: str | None = None,
        payment_status: PayoutPaymentStatus | None = None,
    ) -> PayoutQueueItem:
        normalized_executed_at = _normalize_datetime(executed_at)
        return self.model_copy(
            update={
                "status": PayoutStatus.EXECUTED,
                "execution_id": execution_id,
                "execution_ref_hash": execution_ref_hash,
                "audit_chain_ref": audit_chain_ref,
                "notification_id": notification_id,
                "executed_at": normalized_executed_at,
                "audit_hash": audit_hash,
                "updated_at": normalized_executed_at,
                "payment_connector_name": payment_connector_name,
                "payment_gateway_id": payment_gateway_id,
                "payment_status": payment_status,
                "payment_status_synced_at": normalized_executed_at,
                "payment_error_code": None,
                "payment_refund_id": None,
            }
        )

    def with_payment_status(
        self,
        *,
        payment_status: PayoutPaymentStatus,
        synced_at: datetime,
        audit_hash: str,
        payment_connector_name: str | None = None,
        payment_gateway_id: str | None = None,
        payment_error_code: str | None = None,
        payment_refund_id: str | None = None,
    ) -> PayoutQueueItem:
        normalized_synced_at = _normalize_datetime(synced_at)
        return self.model_copy(
            update={
                "payment_status": payment_status,
                "payment_status_synced_at": normalized_synced_at,
                "payment_connector_name": (
                    payment_connector_name or self.payment_connector_name
                ),
                "payment_gateway_id": payment_gateway_id or self.payment_gateway_id,
                "payment_error_code": payment_error_code,
                "payment_refund_id": payment_refund_id,
                "audit_hash": audit_hash,
                "updated_at": normalized_synced_at,
            }
        )


@dataclass(frozen=True, slots=True)
class PayoutQueueResult:
    payout: PayoutQueueItem
    event: EventEnvelope


@dataclass(slots=True)
class InMemoryPayoutQueueRepository:
    _payouts: dict[tuple[str, str], PayoutQueueItem] = field(default_factory=dict)

    def add_payout(self, payout: PayoutQueueItem) -> PayoutQueueItem:
        key = _payout_key(payout.tenant_id, payout.payout_id)
        if key in self._payouts:
            raise PayoutQueueError("Выплата с таким payout_id уже есть в очереди")

        self._payouts[key] = payout
        return payout

    def get_payout(self, *, tenant_id: str, payout_id: str) -> PayoutQueueItem:
        key = _payout_key(tenant_id, payout_id)
        payout = self._payouts.get(key)
        if payout is None:
            raise PayoutNotFoundError("Выплата не найдена в очереди tenant")
        return payout

    def update_payout(self, payout: PayoutQueueItem) -> PayoutQueueItem:
        key = _payout_key(payout.tenant_id, payout.payout_id)
        if key not in self._payouts:
            raise PayoutNotFoundError("Выплата не найдена в очереди tenant")

        self._payouts[key] = payout
        return payout

    def list_payouts(
        self,
        *,
        tenant_id: str,
        status: PayoutStatus | None = None,
    ) -> tuple[PayoutQueueItem, ...]:
        payouts = (
            payout
            for (record_tenant_id, _payout_id), payout in self._payouts.items()
            if record_tenant_id == tenant_id
        )
        if status is not None:
            payouts = (payout for payout in payouts if payout.status is status)

        return tuple(sorted(payouts, key=lambda payout: payout.created_at))


@dataclass(slots=True)
class PayoutQueueManager:
    publisher: EventPublisher = field(default_factory=InMemoryEventBus)
    repository: InMemoryPayoutQueueRepository = field(
        default_factory=InMemoryPayoutQueueRepository
    )
    audit_logger: AuditLogger = field(default_factory=AuditLogger)
    veto_window_hours: int = DEFAULT_VETO_WINDOW_HOURS

    def __post_init__(self) -> None:
        self.veto_window_hours = validate_veto_window_hours(self.veto_window_hours)

    @classmethod
    def from_env(
        cls,
        *,
        publisher: EventPublisher | None = None,
        repository: InMemoryPayoutQueueRepository | None = None,
        audit_logger: AuditLogger | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> PayoutQueueManager:
        return cls(
            publisher=publisher or InMemoryEventBus(),
            repository=repository or InMemoryPayoutQueueRepository(),
            audit_logger=audit_logger or AuditLogger(),
            veto_window_hours=resolve_veto_window_hours(environ),
        )

    async def queue_payout(
        self,
        *,
        tenant_id: str,
        member_id: str,
        period: str,
        payout_share: float,
        distribution_id: str,
        distribution_hash: str,
        created_by: str,
        correlation_id: str,
        payout_id: str | None = None,
        event_id: str | None = None,
        now: datetime | str | None = None,
        requires_2fa: bool = True,
        metadata: Mapping[str, JSONValue] | None = None,
    ) -> PayoutQueueResult:
        if not requires_2fa:
            raise ValueError("Выплаты требуют 2FA; requires_2fa должен быть True")

        queued_at = _normalize_datetime(now or datetime.now(UTC))
        resolved_payout_id = payout_id or _new_id("payout")
        veto_until = queued_at + timedelta(hours=self.veto_window_hours)
        member_hash = subject_ref_hash(tenant_id=tenant_id, subject_id=member_id)
        created_by_hash = subject_ref_hash(tenant_id=tenant_id, subject_id=created_by)
        audit_record = self.audit_logger.record(
            event_type=PAYOUT_QUEUED_EVENT,
            tenant_id=tenant_id,
            metadata={
                "payout_id": resolved_payout_id,
                "member_hash": member_hash,
                "period": period,
                "distribution_id": distribution_id,
                "distribution_hash": distribution_hash,
                "status": PayoutStatus.QUEUED.value,
                "veto_until": _format_datetime(veto_until),
                "requires_2fa": requires_2fa,
                "created_by_hash": created_by_hash,
                "metadata": dict(metadata or {}),
            },
            timestamp=queued_at,
            correlation_id=correlation_id,
            actor_hash=created_by_hash,
            source=HITL_PAYOUT_SOURCE,
        )
        payout = PayoutQueueItem(
            payout_id=resolved_payout_id,
            tenant_id=tenant_id,
            member_id=member_id,
            member_hash=member_hash,
            period=period,
            payout_share=payout_share,
            distribution_id=distribution_id,
            distribution_hash=distribution_hash,
            status=PayoutStatus.QUEUED,
            veto_until=veto_until,
            requires_2fa=requires_2fa,
            audit_hash=audit_record.audit_hash,
            created_by=created_by,
            created_by_hash=created_by_hash,
            created_at=queued_at,
            updated_at=queued_at,
        )
        self.repository.add_payout(payout)

        event = EventEnvelope(
            event_id=event_id or _new_id("evt-payout-queued"),
            type=PAYOUT_QUEUED_EVENT,
            schema_version=HITL_PAYOUT_SCHEMA_VERSION,
            tenant_id=tenant_id,
            source=HITL_PAYOUT_SOURCE,
            correlation_id=correlation_id,
            occurred_at=queued_at,
            payload={
                "payout_id": payout.payout_id,
                "period": payout.period,
                "veto_until": _format_datetime(payout.veto_until),
                "requires_2fa": payout.requires_2fa,
            },
        )
        await self.publisher.publish(event)
        return PayoutQueueResult(payout=payout, event=event)

    def get_payout(self, *, tenant_id: str, payout_id: str) -> PayoutQueueItem:
        return self.repository.get_payout(tenant_id=tenant_id, payout_id=payout_id)

    def list_payouts(
        self,
        *,
        tenant_id: str,
        status: PayoutStatus | None = None,
    ) -> tuple[PayoutQueueItem, ...]:
        return self.repository.list_payouts(tenant_id=tenant_id, status=status)

    def is_executable(
        self,
        payout: PayoutQueueItem,
        *,
        at: datetime | str | None = None,
    ) -> bool:
        checked_at = _normalize_datetime(at or datetime.now(UTC))
        return (
            payout.status in {PayoutStatus.QUEUED, PayoutStatus.READY_TO_EXECUTE}
            and checked_at >= payout.veto_until
            and payout.confirmation_id is not None
        )

    def mark_ready_for_execution(
        self,
        *,
        tenant_id: str,
        payout_id: str,
        now: datetime | str | None = None,
    ) -> PayoutQueueItem:
        checked_at = _normalize_datetime(now or datetime.now(UTC))
        payout = self.repository.get_payout(tenant_id=tenant_id, payout_id=payout_id)
        if payout.status is PayoutStatus.READY_TO_EXECUTE:
            return payout
        if payout.status is PayoutStatus.CANCELED:
            raise PayoutNotExecutableError("Отменённая выплата не может исполняться")
        if payout.status is PayoutStatus.EXECUTED:
            raise PayoutNotExecutableError("Уже исполненная выплата не меняет статус")
        if checked_at < payout.veto_until:
            raise PayoutNotExecutableError(
                "Выплата не может исполняться до истечения окна вето"
            )
        if payout.confirmation_id is None:
            raise PayoutNotExecutableError(
                "Выплата не может исполняться без 2FA-подтверждения"
            )
        if not self.is_executable(payout, at=checked_at):
            raise PayoutNotExecutableError("Выплата не может исполняться")

        return self.repository.update_payout(
            payout.with_status(
                status=PayoutStatus.READY_TO_EXECUTE,
                updated_at=checked_at,
            )
        )


def resolve_veto_window_hours(environ: Mapping[str, str] | None = None) -> int:
    source = os.environ if environ is None else environ
    raw_value = source.get(VETO_WINDOW_HOURS_ENV)
    if raw_value is None or raw_value.strip() == "":
        return DEFAULT_VETO_WINDOW_HOURS

    try:
        parsed = int(raw_value)
    except ValueError as error:
        raise ValueError("VETO_WINDOW_HOURS должен быть целым числом") from error

    return validate_veto_window_hours(parsed)


def validate_veto_window_hours(value: int) -> int:
    if isinstance(value, bool):
        raise ValueError("VETO_WINDOW_HOURS должен быть целым числом")
    if value < MIN_VETO_WINDOW_HOURS or value > MAX_VETO_WINDOW_HOURS:
        raise ValueError(
            "VETO_WINDOW_HOURS должен быть в диапазоне "
            f"{MIN_VETO_WINDOW_HOURS}-{MAX_VETO_WINDOW_HOURS}"
        )
    return value


def subject_ref_hash(*, tenant_id: str, subject_id: str) -> str:
    return "sha256:" + hashlib.sha256(f"{tenant_id}:{subject_id}".encode()).hexdigest()


def _payout_key(tenant_id: str, payout_id: str) -> tuple[str, str]:
    return tenant_id, payout_id


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


def _new_id(prefix: str) -> IdempotencyKey:
    return f"{prefix}-{uuid4()}"
