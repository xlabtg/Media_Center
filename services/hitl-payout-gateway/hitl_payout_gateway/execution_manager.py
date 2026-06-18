from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from pydantic import Field, field_validator

from hitl_payout_gateway.queue_manager import (
    HITL_PAYOUT_SCHEMA_VERSION,
    HITL_PAYOUT_SOURCE,
    InMemoryPayoutQueueRepository,
    PayoutNotExecutableError,
    PayoutQueueError,
    PayoutQueueItem,
    PayoutStatus,
)
from libs.shared.audit_logger import AuditLogger
from libs.shared.events import EventEnvelope, EventPublisher, InMemoryEventBus
from libs.shared.models import (
    AuditHash,
    CorrelationId,
    EventType,
    IdempotencyKey,
    JSONValue,
    SharedBaseModel,
    SubjectId,
    TenantId,
)

PAYOUT_EXECUTED_EVENT = "payout.executed"
PAYOUT_FAILED_EVENT = "payout.failed"

_CONNECTOR_NAME_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"
_REF_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"
_TEMPLATE_KEY_PATTERN = r"^[a-z][a-z0-9_]{0,127}$"


class PayoutConnectorError(PayoutQueueError):
    """Raised after a connector failure has been logged and published."""

    def __init__(
        self,
        message: str,
        *,
        connector_name: str,
        error_code: str,
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.connector_name = connector_name
        self.error_code = error_code
        self.retryable = retryable


class PayoutPaymentCommand(SharedBaseModel):
    tenant_id: TenantId
    payout_id: IdempotencyKey
    execution_id: IdempotencyKey
    member_id: SubjectId
    member_hash: str = Field(pattern=_REF_HASH_PATTERN)
    period: str = Field(min_length=7, max_length=7)
    payout_share: float = Field(ge=0, le=1, allow_inf_nan=False)
    distribution_id: IdempotencyKey
    distribution_hash: AuditHash
    correlation_id: CorrelationId
    requested_at: datetime
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("requested_at")
    @classmethod
    def _normalize_requested_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class PayoutPaymentResult(SharedBaseModel):
    execution_ref: str = Field(min_length=1, max_length=256)
    connector_name: str = Field(
        default="in_memory_payment",
        pattern=_CONNECTOR_NAME_PATTERN,
    )
    executed_at: datetime

    @field_validator("executed_at")
    @classmethod
    def _normalize_executed_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class BlockchainAuditRecordCommand(SharedBaseModel):
    tenant_id: TenantId
    event_id: IdempotencyKey
    event_type: EventType
    audit_hash: AuditHash
    correlation_id: CorrelationId
    occurred_at: datetime
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def _normalize_occurred_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class BlockchainAuditRecordResult(SharedBaseModel):
    audit_chain_ref: str = Field(min_length=1, max_length=256)
    connector_name: str = Field(
        default="in_memory_blockchain_auditor",
        pattern=_CONNECTOR_NAME_PATTERN,
    )
    recorded_at: datetime

    @field_validator("recorded_at")
    @classmethod
    def _normalize_recorded_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class PayoutNotificationCommand(SharedBaseModel):
    notification_id: IdempotencyKey
    tenant_id: TenantId
    recipient_id: SubjectId
    recipient_hash: str = Field(pattern=_REF_HASH_PATTERN)
    payout_id: IdempotencyKey
    event_type: EventType
    template_key: str = Field(pattern=_TEMPLATE_KEY_PATTERN)
    correlation_id: CorrelationId
    metadata: dict[str, JSONValue] = Field(default_factory=dict)


class PayoutNotificationResult(SharedBaseModel):
    notification_id: IdempotencyKey
    connector_name: str = Field(
        default="in_memory_notification",
        pattern=_CONNECTOR_NAME_PATTERN,
    )
    accepted_at: datetime

    @field_validator("accepted_at")
    @classmethod
    def _normalize_accepted_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class PayoutExecutionReceipt(SharedBaseModel):
    execution_id: IdempotencyKey
    tenant_id: TenantId
    payout_id: IdempotencyKey
    execution_ref_hash: str = Field(pattern=_REF_HASH_PATTERN)
    audit_hash: AuditHash
    audit_chain_ref: str = Field(min_length=1, max_length=256)
    notification_id: IdempotencyKey
    executed_at: datetime
    correlation_id: CorrelationId

    @field_validator("executed_at")
    @classmethod
    def _normalize_executed_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class PaymentConnector(Protocol):
    async def execute_payout(
        self,
        command: PayoutPaymentCommand,
    ) -> PayoutPaymentResult:
        """Execute a payout through an idempotent payment gateway."""


class BlockchainAuditConnector(Protocol):
    async def record_audit_hash(
        self,
        command: BlockchainAuditRecordCommand,
    ) -> BlockchainAuditRecordResult:
        """Record a hash-only payout execution event in the audit-chain."""


class NotificationConnector(Protocol):
    async def notify_payout_executed(
        self,
        command: PayoutNotificationCommand,
    ) -> PayoutNotificationResult:
        """Notify the participant about a completed payout execution."""


@dataclass(slots=True)
class InMemoryPaymentConnector:
    connector_name: str = "in_memory_payment"
    fail_with: PayoutConnectorError | None = None
    _commands: list[PayoutPaymentCommand] = field(default_factory=list, init=False)
    _results: dict[tuple[str, str], PayoutPaymentResult] = field(
        default_factory=dict,
        init=False,
    )

    @property
    def commands(self) -> tuple[PayoutPaymentCommand, ...]:
        return tuple(self._commands)

    async def execute_payout(
        self,
        command: PayoutPaymentCommand,
    ) -> PayoutPaymentResult:
        if self.fail_with is not None:
            raise self.fail_with

        self._commands.append(command)
        key = command.tenant_id, command.execution_id
        existing = self._results.get(key)
        if existing is not None:
            return existing

        result = PayoutPaymentResult(
            execution_ref=f"payment-{command.execution_id}",
            connector_name=self.connector_name,
            executed_at=command.requested_at,
        )
        self._results[key] = result
        return result


@dataclass(slots=True)
class InMemoryBlockchainAuditConnector:
    connector_name: str = "in_memory_blockchain_auditor"
    fail_with: PayoutConnectorError | None = None
    _records: list[BlockchainAuditRecordCommand] = field(
        default_factory=list,
        init=False,
    )

    @property
    def records(self) -> tuple[BlockchainAuditRecordCommand, ...]:
        return tuple(self._records)

    async def record_audit_hash(
        self,
        command: BlockchainAuditRecordCommand,
    ) -> BlockchainAuditRecordResult:
        if self.fail_with is not None:
            raise self.fail_with

        self._records.append(command)
        return BlockchainAuditRecordResult(
            audit_chain_ref=f"audit-chain-{command.event_id}",
            connector_name=self.connector_name,
            recorded_at=command.occurred_at,
        )


@dataclass(slots=True)
class InMemoryNotificationConnector:
    connector_name: str = "in_memory_notification"
    fail_with: PayoutConnectorError | None = None
    _notifications: list[PayoutNotificationCommand] = field(
        default_factory=list,
        init=False,
    )

    @property
    def notifications(self) -> tuple[PayoutNotificationCommand, ...]:
        return tuple(self._notifications)

    async def notify_payout_executed(
        self,
        command: PayoutNotificationCommand,
    ) -> PayoutNotificationResult:
        if self.fail_with is not None:
            raise self.fail_with

        self._notifications.append(command)
        return PayoutNotificationResult(
            notification_id=command.notification_id,
            connector_name=self.connector_name,
            accepted_at=_utcnow(),
        )


@dataclass(slots=True)
class PayoutExecutionManager:
    repository: InMemoryPayoutQueueRepository
    publisher: EventPublisher = field(default_factory=InMemoryEventBus)
    audit_logger: AuditLogger = field(default_factory=AuditLogger)
    payment_connector: PaymentConnector = field(
        default_factory=InMemoryPaymentConnector
    )
    blockchain_auditor: BlockchainAuditConnector = field(
        default_factory=InMemoryBlockchainAuditConnector
    )
    notification_connector: NotificationConnector = field(
        default_factory=InMemoryNotificationConnector
    )
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    async def execute_payout(
        self,
        *,
        tenant_id: str,
        payout_id: str,
        correlation_id: str,
        execution_id: str | None = None,
        event_id: str | None = None,
        failure_event_id: str | None = None,
        notification_id: str | None = None,
        now: datetime | str | None = None,
        metadata: Mapping[str, JSONValue] | None = None,
    ) -> PayoutExecutionReceipt:
        requested_at = _normalize_datetime(now or datetime.now(UTC))
        payout = self.repository.get_payout(tenant_id=tenant_id, payout_id=payout_id)
        _require_executable(payout, at=requested_at)

        resolved_execution_id = execution_id or _new_id("execution")
        resolved_event_id = event_id or _new_id("evt-payout-executed")
        resolved_notification_id = notification_id or _new_id("notification")
        metadata_dict = dict(metadata or {})

        try:
            payment = await self.payment_connector.execute_payout(
                PayoutPaymentCommand(
                    tenant_id=tenant_id,
                    payout_id=payout_id,
                    execution_id=resolved_execution_id,
                    member_id=payout.member_id,
                    member_hash=payout.member_hash,
                    period=payout.period,
                    payout_share=payout.payout_share,
                    distribution_id=payout.distribution_id,
                    distribution_hash=payout.distribution_hash,
                    correlation_id=correlation_id,
                    requested_at=requested_at,
                    metadata=metadata_dict,
                )
            )
        except Exception as error:
            connector_error = await self._record_connector_failure(
                payout=payout,
                connector_name="payment_gateway",
                error=error,
                occurred_at=requested_at,
                correlation_id=correlation_id,
                failure_event_id=failure_event_id,
                metadata=metadata_dict,
            )
            raise connector_error from error

        executed_at = payment.executed_at
        execution_ref_hash = _hash_ref(
            tenant_id=tenant_id,
            value=payment.execution_ref,
        )
        audit_record = self.audit_logger.record(
            event_type=PAYOUT_EXECUTED_EVENT,
            tenant_id=tenant_id,
            metadata={
                "payout_id": payout.payout_id,
                "execution_id": resolved_execution_id,
                "execution_ref_hash": execution_ref_hash,
                "payment_connector": payment.connector_name,
                "previous_status": payout.status.value,
                "new_status": PayoutStatus.EXECUTED.value,
                "confirmation_id": payout.confirmation_id,
                "distribution_hash": payout.distribution_hash,
                "metadata": metadata_dict,
            },
            timestamp=executed_at,
            correlation_id=correlation_id,
            source=HITL_PAYOUT_SOURCE,
        )

        try:
            audit_chain = await self.blockchain_auditor.record_audit_hash(
                BlockchainAuditRecordCommand(
                    tenant_id=tenant_id,
                    event_id=resolved_event_id,
                    event_type=PAYOUT_EXECUTED_EVENT,
                    audit_hash=audit_record.audit_hash,
                    correlation_id=correlation_id,
                    occurred_at=executed_at,
                    metadata={
                        "payout_id": payout.payout_id,
                        "execution_id": resolved_execution_id,
                        "execution_ref_hash": execution_ref_hash,
                        "source": HITL_PAYOUT_SOURCE,
                    },
                )
            )
        except Exception as error:
            connector_error = await self._record_connector_failure(
                payout=payout,
                connector_name="blockchain_auditor",
                error=error,
                occurred_at=executed_at,
                correlation_id=correlation_id,
                failure_event_id=failure_event_id,
                metadata=metadata_dict,
            )
            raise connector_error from error

        try:
            notification = await self.notification_connector.notify_payout_executed(
                PayoutNotificationCommand(
                    notification_id=resolved_notification_id,
                    tenant_id=tenant_id,
                    recipient_id=payout.member_id,
                    recipient_hash=payout.member_hash,
                    payout_id=payout.payout_id,
                    event_type=PAYOUT_EXECUTED_EVENT,
                    template_key="hitl_payout_executed",
                    correlation_id=correlation_id,
                    metadata={
                        "payout_id": payout.payout_id,
                        "execution_ref_hash": execution_ref_hash,
                        "audit_hash": audit_record.audit_hash,
                        "audit_chain_ref": audit_chain.audit_chain_ref,
                    },
                )
            )
        except Exception as error:
            connector_error = await self._record_connector_failure(
                payout=payout,
                connector_name="notification_gateway",
                error=error,
                occurred_at=executed_at,
                correlation_id=correlation_id,
                failure_event_id=failure_event_id,
                metadata=metadata_dict,
            )
            raise connector_error from error

        updated = self.repository.update_payout(
            payout.with_execution(
                execution_id=resolved_execution_id,
                execution_ref_hash=execution_ref_hash,
                audit_chain_ref=audit_chain.audit_chain_ref,
                notification_id=notification.notification_id,
                executed_at=executed_at,
                audit_hash=audit_record.audit_hash,
            )
        )
        event = EventEnvelope(
            event_id=resolved_event_id,
            type=PAYOUT_EXECUTED_EVENT,
            schema_version=HITL_PAYOUT_SCHEMA_VERSION,
            tenant_id=tenant_id,
            source=HITL_PAYOUT_SOURCE,
            correlation_id=correlation_id,
            occurred_at=executed_at,
            payload={
                "payout_id": updated.payout_id,
                "execution_ref_hash": execution_ref_hash,
                "status": updated.status.value,
                "audit_hash": audit_record.audit_hash,
            },
        )
        await self.publisher.publish(event)
        return PayoutExecutionReceipt(
            execution_id=resolved_execution_id,
            tenant_id=tenant_id,
            payout_id=payout_id,
            execution_ref_hash=execution_ref_hash,
            audit_hash=audit_record.audit_hash,
            audit_chain_ref=audit_chain.audit_chain_ref,
            notification_id=notification.notification_id,
            executed_at=executed_at,
            correlation_id=correlation_id,
        )

    async def _record_connector_failure(
        self,
        *,
        payout: PayoutQueueItem,
        connector_name: str,
        error: Exception,
        occurred_at: datetime,
        correlation_id: str,
        failure_event_id: str | None,
        metadata: Mapping[str, JSONValue],
    ) -> PayoutConnectorError:
        connector_error = _as_connector_error(
            error,
            connector_name=connector_name,
        )
        failure_audit = self.audit_logger.record(
            event_type=PAYOUT_FAILED_EVENT,
            tenant_id=payout.tenant_id,
            metadata={
                "payout_id": payout.payout_id,
                "connector": connector_error.connector_name,
                "error_code": connector_error.error_code,
                "retryable": connector_error.retryable,
                "previous_status": payout.status.value,
                "metadata": dict(metadata),
            },
            timestamp=occurred_at,
            correlation_id=correlation_id,
            source=HITL_PAYOUT_SOURCE,
        )
        self.logger.warning(
            "Сбой коннектора исполнения выплаты",
            extra={
                "tenant_id": payout.tenant_id,
                "payout_id": payout.payout_id,
                "connector": connector_error.connector_name,
                "error_code": connector_error.error_code,
                "retryable": connector_error.retryable,
                "correlation_id": correlation_id,
            },
        )
        event = EventEnvelope(
            event_id=failure_event_id or _new_id("evt-payout-failed"),
            type=PAYOUT_FAILED_EVENT,
            schema_version=HITL_PAYOUT_SCHEMA_VERSION,
            tenant_id=payout.tenant_id,
            source=HITL_PAYOUT_SOURCE,
            correlation_id=correlation_id,
            occurred_at=occurred_at,
            payload={
                "payout_id": payout.payout_id,
                "error_code": connector_error.error_code,
                "retryable": connector_error.retryable,
                "connector": connector_error.connector_name,
                "audit_hash": failure_audit.audit_hash,
            },
        )
        await self.publisher.publish(event)
        return connector_error


def _require_executable(payout: PayoutQueueItem, *, at: datetime) -> None:
    if payout.status is PayoutStatus.CANCELED:
        raise PayoutNotExecutableError("Отменённая выплата не может исполняться")
    if payout.status is PayoutStatus.EXECUTED:
        raise PayoutNotExecutableError("Выплата уже исполнена")
    if payout.status not in {PayoutStatus.QUEUED, PayoutStatus.READY_TO_EXECUTE}:
        raise PayoutNotExecutableError("Выплата не может исполняться")
    if at < payout.veto_until:
        raise PayoutNotExecutableError(
            "Выплата не может исполняться до истечения окна вето"
        )
    if payout.confirmation_id is None:
        raise PayoutNotExecutableError(
            "Выплата не может исполняться без 2FA-подтверждения"
        )


def _as_connector_error(
    error: Exception,
    *,
    connector_name: str,
) -> PayoutConnectorError:
    if isinstance(error, PayoutConnectorError):
        return error

    return PayoutConnectorError(
        "Сбой коннектора исполнения выплаты",
        connector_name=connector_name,
        error_code="connector_error",
        retryable=True,
    )


def _hash_ref(*, tenant_id: str, value: str) -> str:
    return "sha256:" + hashlib.sha256(f"{tenant_id}:{value}".encode()).hexdigest()


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


def _utcnow() -> datetime:
    return datetime.now(UTC)
