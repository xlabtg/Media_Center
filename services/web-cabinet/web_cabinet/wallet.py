from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import Field

from libs.shared.errors import SharedError
from libs.shared.models import (
    AuditHash,
    IdempotencyKey,
    JSONValue,
    SharedBaseModel,
    SubjectId,
    TenantId,
)
from libs.shared.tenant import TenantContext, TenantScopedRepository

_ZERO_MCV = Decimal("0.00")
_MCV_QUANT = Decimal("0.01")


class WalletOperationType(StrEnum):
    DISTRIBUTION_CREDIT = "distribution_credit"
    PAYOUT_DEBIT = "payout_debit"
    MANUAL_ADJUSTMENT = "manual_adjustment"


class WalletOperationResponse(SharedBaseModel):
    operation_id: IdempotencyKey
    tenant_id: TenantId
    member_id: SubjectId
    member_hash: str
    amount_mcv: Decimal = Field(max_digits=14, decimal_places=2)
    balance_after_mcv: Decimal = Field(max_digits=14, decimal_places=2)
    type: WalletOperationType
    ref_type: str
    ref_id: IdempotencyKey
    period: str | None = None
    distribution_hash: AuditHash | None = None
    payout_share: float | None = None
    metadata: dict[str, JSONValue] = Field(default_factory=dict)
    audit_hash: AuditHash
    idempotency_key: IdempotencyKey
    created_by: SubjectId
    created_by_hash: str
    created_at: datetime


class WalletBalanceResponse(SharedBaseModel):
    tenant_id: TenantId
    member_id: SubjectId
    balance_mcv: Decimal = Field(max_digits=14, decimal_places=2)
    credited_mcv: Decimal = Field(max_digits=14, decimal_places=2)
    debited_mcv: Decimal = Field(max_digits=14, decimal_places=2)
    operation_count: int = Field(ge=0)


@dataclass(frozen=True, slots=True)
class WalletOperationRecord:
    operation_id: str
    tenant_id: str
    member_id: str
    member_hash: str
    amount_mcv: Decimal
    balance_after_mcv: Decimal
    type: str
    ref_type: str
    ref_id: str
    period: str | None
    distribution_hash: str | None
    payout_share: float | None
    metadata: dict[str, JSONValue]
    audit_hash: str
    idempotency_key: str
    request_hash: str
    created_by: str
    created_by_hash: str
    created_at: datetime


@dataclass(slots=True)
class WalletBalance:
    tenant_id: str
    member_id: str
    balance_mcv: Decimal
    credited_mcv: Decimal
    debited_mcv: Decimal
    operation_count: int


@dataclass(slots=True)
class InMemoryWalletRepository:
    _operations: list[WalletOperationRecord] = field(default_factory=list)
    _tenant_guard: TenantScopedRepository[WalletOperationRecord] = field(
        default_factory=lambda: TenantScopedRepository("wallet_operations")
    )

    def operation_exists(self, *, tenant_id: str, operation_id: str) -> bool:
        return any(
            record.tenant_id == tenant_id and record.operation_id == operation_id
            for record in self._operations
        )

    def add_operation(self, record: WalletOperationRecord) -> WalletOperationRecord:
        if self.operation_exists(
            tenant_id=record.tenant_id,
            operation_id=record.operation_id,
        ):
            raise SharedError(
                status_code=409,
                error_code="wallet_operation_conflict",
                message="Операция кошелька с таким operation_id уже существует",
            )

        self._operations.append(record)
        return record

    def list_operations(
        self,
        *,
        context: TenantContext,
        member_id: str | None = None,
        ref_type: str | None = None,
        ref_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[WalletOperationRecord, ...]:
        records = self._tenant_guard.list_for_tenant(self._operations, context)
        filtered = (
            record
            for record in records
            if (member_id is None or record.member_id == member_id)
            and (ref_type is None or record.ref_type == ref_type)
            and (ref_id is None or record.ref_id == ref_id)
        )
        sorted_records = sorted(
            filtered,
            key=lambda record: (record.created_at, record.operation_id),
            reverse=True,
        )
        return tuple(sorted_records[offset : offset + limit])

    def balance_for_member(
        self,
        *,
        context: TenantContext,
        member_id: str,
    ) -> WalletBalance:
        records = tuple(
            record
            for record in self._tenant_guard.list_for_tenant(self._operations, context)
            if record.member_id == member_id
        )
        credited = _sum_mcv(
            record.amount_mcv for record in records if record.amount_mcv > _ZERO_MCV
        )
        debited = _sum_mcv(
            -record.amount_mcv for record in records if record.amount_mcv < _ZERO_MCV
        )
        return WalletBalance(
            tenant_id=context.tenant_id,
            member_id=member_id,
            balance_mcv=_normalize_mcv(credited - debited),
            credited_mcv=credited,
            debited_mcv=debited,
            operation_count=len(records),
        )


def _sum_mcv(values: Iterable[Decimal]) -> Decimal:
    total = Decimal("0")
    for value in values:
        total += value
    return _normalize_mcv(total)


def _normalize_mcv(value: Decimal) -> Decimal:
    return value.quantize(_MCV_QUANT)
