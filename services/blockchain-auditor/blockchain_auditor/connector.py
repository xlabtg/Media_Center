from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, cast

from pydantic import Field, field_validator

from libs.shared import (
    AuditHash,
    CorrelationId,
    EventType,
    IdempotencyKey,
    JSONValue,
    SharedBaseModel,
    TenantId,
)
from libs.shared.tenant import TenantContext

from .access_controller import BlockchainAuditAccessController
from .settings import BlockchainAuditorSettings

_CONNECTOR_NAME_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"
_FORBIDDEN_METADATA_KEYS = frozenset(
    {
        "access_token",
        "amount",
        "amount_rub",
        "content",
        "email",
        "full_name",
        "member_id",
        "password",
        "payout_amount",
        "payout_share",
        "phone",
        "raw_content",
        "recipient_id",
        "refresh_token",
        "secret",
        "text",
        "token",
        "transcript",
        "voice",
    }
)


class BlockchainAuditError(Exception):
    """Base error for the blockchain auditor service layer."""


class AuditRecordConflictError(BlockchainAuditError):
    """Raised when an event id is reused with another audit hash."""


class AuditBatchError(BlockchainAuditError):
    """Raised when a batch write request cannot be prepared."""


class AuditMetadataPolicyError(BlockchainAuditError):
    """Raised when chain metadata contains forbidden sensitive fields."""


class AuditRecordCommand(SharedBaseModel):
    tenant_id: TenantId
    event_id: IdempotencyKey
    event_type: EventType
    audit_hash: AuditHash
    occurred_at: datetime
    correlation_id: CorrelationId | None = None
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def _normalize_occurred_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class AuditRecordReceipt(SharedBaseModel):
    tenant_id: TenantId
    event_id: IdempotencyKey
    audit_hash: AuditHash
    block_ref: str = Field(min_length=1, max_length=512)
    connector_name: str = Field(pattern=_CONNECTOR_NAME_PATTERN)
    recorded_at: datetime

    @field_validator("recorded_at")
    @classmethod
    def _normalize_recorded_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class AuditRecord(AuditRecordReceipt):
    event_type: EventType
    occurred_at: datetime
    correlation_id: CorrelationId | None = None
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def _normalize_occurred_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class GrpcBlockchainAuditTransport(Protocol):
    async def record_audit_hash(
        self,
        endpoint_url: str,
        command: AuditRecordCommand,
    ) -> AuditRecordReceipt:
        """Write a hash-only audit record through a gRPC backend."""

    async def record_audit_hashes(
        self,
        endpoint_url: str,
        commands: tuple[AuditRecordCommand, ...],
    ) -> tuple[AuditRecordReceipt, ...]:
        """Write a batch of hash-only audit records through a gRPC backend."""

    async def get_audit_record(
        self,
        endpoint_url: str,
        *,
        tenant_id: str,
        event_id: str,
    ) -> AuditRecord | None:
        """Read one audit record through a gRPC backend."""


@dataclass(frozen=True, slots=True)
class GrpcBlockchainAuditConnector:
    settings: BlockchainAuditorSettings
    transport: GrpcBlockchainAuditTransport
    access_controller: BlockchainAuditAccessController = field(
        default_factory=BlockchainAuditAccessController,
    )

    async def record_audit_hash(
        self,
        command: AuditRecordCommand,
        *,
        context: TenantContext | None = None,
    ) -> AuditRecordReceipt:
        self.access_controller.require_record_access(
            tenant_id=command.tenant_id,
            context=context,
        )
        return await self.transport.record_audit_hash(
            self.settings.blockchain_auditor_url,
            _safe_command(command),
        )

    async def record_audit_hashes(
        self,
        commands: Iterable[AuditRecordCommand],
        *,
        context: TenantContext | None = None,
    ) -> tuple[AuditRecordReceipt, ...]:
        batch = _normalize_batch(commands)
        self.access_controller.require_batch_record_access(
            tenant_ids=(command.tenant_id for command in batch),
            context=context,
        )
        safe_batch = tuple(_safe_command(command) for command in batch)
        return await self.transport.record_audit_hashes(
            self.settings.blockchain_auditor_url,
            safe_batch,
        )

    async def get_audit_record(
        self,
        *,
        tenant_id: str,
        event_id: str,
        context: TenantContext | None = None,
    ) -> AuditRecord | None:
        self.access_controller.require_read_access(
            tenant_id=tenant_id,
            context=context,
        )
        return await self.transport.get_audit_record(
            self.settings.blockchain_auditor_url,
            tenant_id=tenant_id,
            event_id=event_id,
        )


@dataclass(slots=True)
class InMemoryGrpcBlockchainAuditTransport:
    connector_name: str = "grpc_blockchain_auditor"
    _records: dict[tuple[str, str], AuditRecord] = field(
        default_factory=dict,
        init=False,
    )
    _record_requests: list[AuditRecordCommand] = field(
        default_factory=list,
        init=False,
    )
    _batch_record_requests: list[tuple[AuditRecordCommand, ...]] = field(
        default_factory=list,
        init=False,
    )

    @property
    def record_requests(self) -> tuple[AuditRecordCommand, ...]:
        return tuple(self._record_requests)

    @property
    def batch_record_requests(self) -> tuple[tuple[AuditRecordCommand, ...], ...]:
        return tuple(self._batch_record_requests)

    async def record_audit_hash(
        self,
        endpoint_url: str,
        command: AuditRecordCommand,
    ) -> AuditRecordReceipt:
        self._record_requests.append(command)
        return (await self._record_batch(endpoint_url, (command,)))[0]

    async def record_audit_hashes(
        self,
        endpoint_url: str,
        commands: tuple[AuditRecordCommand, ...],
    ) -> tuple[AuditRecordReceipt, ...]:
        self._batch_record_requests.append(commands)
        return await self._record_batch(endpoint_url, commands)

    async def _record_batch(
        self,
        endpoint_url: str,
        commands: tuple[AuditRecordCommand, ...],
    ) -> tuple[AuditRecordReceipt, ...]:
        staged_records = dict(self._records)
        new_records: dict[tuple[str, str], AuditRecord] = {}
        receipts: list[AuditRecordReceipt] = []

        for command in commands:
            record = _record_for_command(
                endpoint_url=endpoint_url,
                connector_name=self.connector_name,
                command=command,
                staged_records=staged_records,
            )
            staged_records[(command.tenant_id, command.event_id)] = record
            if (command.tenant_id, command.event_id) not in self._records:
                new_records[(command.tenant_id, command.event_id)] = record
            receipts.append(_receipt_from_record(record))

        self._records.update(new_records)
        return tuple(receipts)

    async def get_audit_record(
        self,
        endpoint_url: str,
        *,
        tenant_id: str,
        event_id: str,
    ) -> AuditRecord | None:
        return self._records.get((tenant_id, event_id))


def _record_for_command(
    *,
    endpoint_url: str,
    connector_name: str,
    command: AuditRecordCommand,
    staged_records: dict[tuple[str, str], AuditRecord],
) -> AuditRecord:
    key = command.tenant_id, command.event_id
    existing = staged_records.get(key)
    if existing is not None:
        if existing.audit_hash != command.audit_hash:
            raise AuditRecordConflictError("event_id уже записан с другим audit_hash")
        return existing

    recorded_at = command.occurred_at
    return AuditRecord(
        tenant_id=command.tenant_id,
        event_id=command.event_id,
        event_type=command.event_type,
        audit_hash=command.audit_hash,
        block_ref=_block_ref(
            endpoint_url=endpoint_url,
            tenant_id=command.tenant_id,
            event_id=command.event_id,
        ),
        connector_name=connector_name,
        occurred_at=command.occurred_at,
        recorded_at=recorded_at,
        correlation_id=command.correlation_id,
        metadata=_clone_metadata(command.metadata),
    )


def validate_audit_metadata(
    metadata: Mapping[str, JSONValue],
) -> dict[str, JSONValue]:
    cloned = _clone_metadata(metadata)
    forbidden_path = _find_forbidden_metadata_key(cloned)
    if forbidden_path is not None:
        raise AuditMetadataPolicyError(
            f"metadata содержит запрещённый ключ {forbidden_path}"
        )

    return cloned


def _normalize_batch(
    commands: Iterable[AuditRecordCommand],
) -> tuple[AuditRecordCommand, ...]:
    batch = tuple(commands)
    if not batch:
        raise AuditBatchError("batch должен содержать хотя бы одну audit-запись")

    return batch


def _safe_command(command: AuditRecordCommand) -> AuditRecordCommand:
    return command.model_copy(
        update={"metadata": validate_audit_metadata(command.metadata)}
    )


def _receipt_from_record(record: AuditRecord) -> AuditRecordReceipt:
    return AuditRecordReceipt(
        tenant_id=record.tenant_id,
        event_id=record.event_id,
        audit_hash=record.audit_hash,
        block_ref=record.block_ref,
        connector_name=record.connector_name,
        recorded_at=record.recorded_at,
    )


def _clone_metadata(metadata: Mapping[str, JSONValue]) -> dict[str, JSONValue]:
    return cast(
        dict[str, JSONValue],
        json.loads(json.dumps(dict(metadata), sort_keys=True)),
    )


def _find_forbidden_metadata_key(
    value: JSONValue, path: str = "metadata"
) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if _normalize_metadata_key(key) in _FORBIDDEN_METADATA_KEYS:
                return child_path

            nested_path = _find_forbidden_metadata_key(item, child_path)
            if nested_path is not None:
                return nested_path

    if isinstance(value, list):
        for index, item in enumerate(value):
            nested_path = _find_forbidden_metadata_key(item, f"{path}[{index}]")
            if nested_path is not None:
                return nested_path

    return None


def _normalize_metadata_key(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


def _block_ref(*, endpoint_url: str, tenant_id: str, event_id: str) -> str:
    return f"{endpoint_url.rstrip('/')}/audit/{tenant_id}/{event_id}"
