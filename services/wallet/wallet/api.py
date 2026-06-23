from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request
from fastapi import status as http_status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, Field, field_validator

from libs.shared import (
    BOARD_ROLE,
    COUNCIL_ROLE,
    IDEMPOTENCY_CONFLICT_CODE,
    PRESIDIUM_ROLE,
    VALIDATION_ERROR_CODE,
    AccessPolicy,
    AuditHash,
    AuditLogger,
    BaseAppConfig,
    CorrelationId,
    IdempotencyKey,
    InMemoryAuditLogSink,
    InMemoryAuditSink,
    InMemoryEventBus,
    JSONValue,
    ServiceTemplateConfig,
    SharedBaseModel,
    SharedError,
    SubjectId,
    TenantContext,
    TenantCoreError,
    TenantId,
    TenantScopedRepository,
    create_service_runtime_app,
    error_response_body,
    require_access,
    require_tenant_context,
)
from libs.shared.events import EventEnvelope

WALLET_SERVICE_NAME = "wallet"
WALLET_SOURCE = "wallet"
WALLET_SCHEMA_VERSION = "1.0"
WALLET_OPERATION_RECORDED_EVENT = "wallet.operation_recorded"

_IDEMPOTENCY_HEADER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"
_PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
_REFERENCE_TYPE_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
_MCV_QUANT = Decimal("0.01")
_ZERO_MCV = Decimal("0.00")

IdempotencyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        pattern=_IDEMPOTENCY_HEADER_PATTERN,
    ),
]

WALLET_RECORD_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    BOARD_ROLE,
    action="wallet.operation.record",
    resource_type="wallet",
)
WALLET_READ_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    action="wallet.read",
    resource_type="wallet",
)
_WALLET_GOVERNANCE_READ_ROLES = frozenset((COUNCIL_ROLE, PRESIDIUM_ROLE, BOARD_ROLE))


class WalletOperationType(StrEnum):
    DISTRIBUTION_CREDIT = "distribution_credit"
    PAYOUT_DEBIT = "payout_debit"
    MANUAL_ADJUSTMENT = "manual_adjustment"


class WalletOperationCreateRequest(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    operation_id: IdempotencyKey | None = None
    member_id: SubjectId
    amount_mcv: Decimal = Field(max_digits=14, decimal_places=2)
    type: WalletOperationType
    ref_type: str = Field(pattern=_REFERENCE_TYPE_PATTERN)
    ref_id: IdempotencyKey
    period: str | None = Field(default=None, pattern=_PERIOD_PATTERN)
    distribution_hash: AuditHash | None = None
    payout_share: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    created_at: datetime | None = None
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("amount_mcv")
    @classmethod
    def _normalize_amount(cls, value: Decimal) -> Decimal:
        normalized = value.quantize(_MCV_QUANT)
        if normalized == _ZERO_MCV:
            raise ValueError("amount_mcv не может быть нулевым")
        return normalized

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_type(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("created_at")
    @classmethod
    def _normalize_optional_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _normalize_datetime(value)


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


class WalletOperationListResponse(SharedBaseModel):
    items: tuple[WalletOperationResponse, ...]


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

    def get_existing_operation(
        self,
        *,
        context: TenantContext,
        idempotency_key: str,
        request_hash: str,
    ) -> WalletOperationRecord | None:
        for record in self._operations:
            if (
                record.tenant_id == context.tenant_id
                and record.idempotency_key == idempotency_key
            ):
                if record.request_hash != request_hash:
                    raise SharedError(
                        status_code=409,
                        error_code=IDEMPOTENCY_CONFLICT_CODE,
                        message="Idempotency-Key уже использован с другим payload",
                        correlation_id=context.correlation_id,
                    )
                return record

        return None

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


@dataclass(slots=True)
class WalletAPIState:
    repository: InMemoryWalletRepository
    publisher: InMemoryEventBus
    audit_logger: AuditLogger
    audit_log_sink: InMemoryAuditLogSink
    tenant_audit_sink: InMemoryAuditSink


router = APIRouter(tags=["Wallet"])


def create_wallet_app(
    config: BaseAppConfig | ServiceTemplateConfig,
    *,
    repository: InMemoryWalletRepository | None = None,
    publisher: InMemoryEventBus | None = None,
    audit_log_sink: InMemoryAuditLogSink | None = None,
    tenant_audit_sink: InMemoryAuditSink | None = None,
) -> FastAPI:
    resolved_repository = repository or InMemoryWalletRepository()
    resolved_publisher = publisher or InMemoryEventBus()
    resolved_audit_log_sink = audit_log_sink or InMemoryAuditLogSink()
    resolved_tenant_audit_sink = tenant_audit_sink or InMemoryAuditSink()
    audit_logger = AuditLogger(sink=resolved_audit_log_sink)
    app = create_service_runtime_app(
        config,
        title="Media Center Wallet",
        audit_sink=resolved_tenant_audit_sink,
    )
    app.state.wallet_api = WalletAPIState(
        repository=resolved_repository,
        publisher=resolved_publisher,
        audit_logger=audit_logger,
        audit_log_sink=resolved_audit_log_sink,
        tenant_audit_sink=resolved_tenant_audit_sink,
    )
    app.add_exception_handler(TenantCoreError, _tenant_core_error_handler)
    app.add_exception_handler(SharedError, _shared_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(ValueError, _value_error_handler)
    app.include_router(router)
    return app


@router.post(
    "/wallet/operations",
    response_model=WalletOperationResponse,
    status_code=http_status.HTTP_201_CREATED,
    summary="Записать МСЦ-операцию участника",
)
async def record_wallet_operation(
    payload: WalletOperationCreateRequest,
    idempotency_key: IdempotencyHeader,
    state: Annotated[WalletAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> WalletOperationResponse:
    actor_context = require_access(WALLET_RECORD_POLICY, context=context)
    request_hash = _hash_json_payload(
        {
            "tenant_id": context.tenant_id,
            "idempotency_key": idempotency_key,
            "payload": cast(
                dict[str, JSONValue],
                payload.model_dump(mode="json", exclude_none=True),
            ),
        }
    )
    existing_record = state.repository.get_existing_operation(
        context=context,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if existing_record is not None:
        return _operation_response(existing_record)

    operation_id = payload.operation_id or _new_id("wallet-op")
    if state.repository.operation_exists(
        tenant_id=context.tenant_id,
        operation_id=operation_id,
    ):
        raise SharedError(
            status_code=409,
            error_code="wallet_operation_conflict",
            message="Операция кошелька с таким operation_id уже существует",
            correlation_id=context.correlation_id,
        )

    created_at = payload.created_at or datetime.now(UTC)
    created_by = _subject(actor_context)
    member_hash = subject_ref_hash(
        tenant_id=context.tenant_id,
        subject_id=payload.member_id,
    )
    created_by_hash = subject_ref_hash(
        tenant_id=context.tenant_id,
        subject_id=created_by,
    )
    current_balance = state.repository.balance_for_member(
        context=context,
        member_id=payload.member_id,
    )
    balance_after_mcv = _normalize_mcv(current_balance.balance_mcv + payload.amount_mcv)
    audit_record = state.audit_logger.record(
        event_type=WALLET_OPERATION_RECORDED_EVENT,
        tenant_id=context.tenant_id,
        metadata={
            "operation_id": operation_id,
            "member_hash": member_hash,
            "amount_mcv": _mcv_to_string(payload.amount_mcv),
            "balance_after_mcv": _mcv_to_string(balance_after_mcv),
            "type": payload.type.value,
            "ref_type": payload.ref_type,
            "ref_id": payload.ref_id,
            "period": payload.period,
            "distribution_hash": payload.distribution_hash,
            "payout_share": payload.payout_share,
            "created_by_hash": created_by_hash,
            "metadata": payload.metadata,
        },
        timestamp=created_at,
        correlation_id=_correlation_id(context),
        actor_hash=created_by_hash,
        source=WALLET_SOURCE,
    )
    record = state.repository.add_operation(
        WalletOperationRecord(
            operation_id=operation_id,
            tenant_id=context.tenant_id,
            member_id=payload.member_id,
            member_hash=member_hash,
            amount_mcv=payload.amount_mcv,
            balance_after_mcv=balance_after_mcv,
            type=payload.type.value,
            ref_type=payload.ref_type,
            ref_id=payload.ref_id,
            period=payload.period,
            distribution_hash=payload.distribution_hash,
            payout_share=payload.payout_share,
            metadata=payload.metadata,
            audit_hash=audit_record.audit_hash,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            created_by=created_by,
            created_by_hash=created_by_hash,
            created_at=created_at,
        )
    )
    event = EventEnvelope(
        event_id=_new_id("evt-wallet-operation-recorded"),
        type=WALLET_OPERATION_RECORDED_EVENT,
        schema_version=WALLET_SCHEMA_VERSION,
        tenant_id=context.tenant_id,
        source=WALLET_SOURCE,
        correlation_id=_correlation_id(context),
        occurred_at=created_at,
        payload={
            "operation_id": record.operation_id,
            "member_hash": record.member_hash,
            "type": record.type,
            "ref_type": record.ref_type,
            "ref_id": record.ref_id,
            "audit_hash": record.audit_hash,
        },
    )
    await state.publisher.publish(event)
    return _operation_response(record)


@router.get(
    "/wallet/balance",
    response_model=WalletBalanceResponse,
    summary="Получить баланс МСЦ участника",
)
def get_wallet_balance(
    state: Annotated[WalletAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
    member_id: Annotated[SubjectId | None, Query()] = None,
) -> WalletBalanceResponse:
    target_member_id = member_id or _subject(context)
    _ensure_member_read_allowed(context, target_member_id)
    return _balance_response(
        state.repository.balance_for_member(
            context=context,
            member_id=target_member_id,
        )
    )


@router.get(
    "/wallet/operations",
    response_model=WalletOperationListResponse,
    summary="Получить историю МСЦ-операций tenant",
)
def list_wallet_operations(
    state: Annotated[WalletAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
    member_id: Annotated[SubjectId | None, Query()] = None,
    ref_type: Annotated[str | None, Query(pattern=_REFERENCE_TYPE_PATTERN)] = None,
    ref_id: Annotated[IdempotencyKey | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> WalletOperationListResponse:
    target_member_id = member_id
    if target_member_id is None and not _has_governance_read_role(context):
        target_member_id = _subject(context)
    if target_member_id is not None:
        _ensure_member_read_allowed(context, target_member_id)
    else:
        require_access(WALLET_READ_POLICY, context=context)

    return WalletOperationListResponse(
        items=tuple(
            _operation_response(record)
            for record in state.repository.list_operations(
                context=context,
                member_id=target_member_id,
                ref_type=ref_type,
                ref_id=ref_id,
                limit=limit,
                offset=offset,
            )
        )
    )


def subject_ref_hash(*, tenant_id: str, subject_id: str) -> str:
    return "sha256:" + hashlib.sha256(f"{tenant_id}:{subject_id}".encode()).hexdigest()


def _api_state(request: Request) -> WalletAPIState:
    return cast(WalletAPIState, request.app.state.wallet_api)


def _tenant_context() -> TenantContext:
    return require_tenant_context()


def _subject(context: TenantContext) -> SubjectId:
    if context.subject is None or context.subject.strip() == "":
        raise SharedError(
            status_code=400,
            error_code="actor_required",
            message="Операция wallet требует subject в tenant context",
            correlation_id=context.correlation_id,
        )

    return context.subject


def _ensure_member_read_allowed(context: TenantContext, member_id: str) -> None:
    if context.subject == member_id:
        return

    require_access(WALLET_READ_POLICY, context=context)


def _has_governance_read_role(context: TenantContext) -> bool:
    return bool(_WALLET_GOVERNANCE_READ_ROLES.intersection(context.roles))


def _correlation_id(context: TenantContext) -> CorrelationId:
    return context.correlation_id or f"corr-{uuid4()}"


def _operation_response(record: WalletOperationRecord) -> WalletOperationResponse:
    return WalletOperationResponse(
        operation_id=record.operation_id,
        tenant_id=record.tenant_id,
        member_id=record.member_id,
        member_hash=record.member_hash,
        amount_mcv=record.amount_mcv,
        balance_after_mcv=record.balance_after_mcv,
        type=WalletOperationType(record.type),
        ref_type=record.ref_type,
        ref_id=record.ref_id,
        period=record.period,
        distribution_hash=record.distribution_hash,
        payout_share=record.payout_share,
        metadata=record.metadata,
        audit_hash=record.audit_hash,
        idempotency_key=record.idempotency_key,
        created_by=record.created_by,
        created_by_hash=record.created_by_hash,
        created_at=record.created_at,
    )


def _balance_response(balance: WalletBalance) -> WalletBalanceResponse:
    return WalletBalanceResponse(
        tenant_id=balance.tenant_id,
        member_id=balance.member_id,
        balance_mcv=balance.balance_mcv,
        credited_mcv=balance.credited_mcv,
        debited_mcv=balance.debited_mcv,
        operation_count=balance.operation_count,
    )


def _sum_mcv(values: Iterable[Decimal]) -> Decimal:
    total = _ZERO_MCV
    for value in values:
        total += value
    return _normalize_mcv(total)


def _normalize_mcv(value: Decimal) -> Decimal:
    return value.quantize(_MCV_QUANT)


def _mcv_to_string(value: Decimal) -> str:
    return f"{_normalize_mcv(value):.2f}"


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _hash_json_payload(payload: Mapping[str, JSONValue]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _new_id(prefix: str) -> IdempotencyKey:
    return f"{prefix}-{uuid4()}"


async def _tenant_core_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    error = cast(TenantCoreError, exc)
    return JSONResponse(
        status_code=error.status_code,
        content=error.to_response_body(),
    )


async def _shared_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    error = cast(SharedError, exc)
    return JSONResponse(
        status_code=error.status_code,
        content=error.to_response_body(),
    )


async def _validation_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    validation_error = cast(RequestValidationError, exc)
    return JSONResponse(
        status_code=400,
        content=jsonable_encoder(
            error_response_body(
                code=VALIDATION_ERROR_CODE,
                message="Запрос не прошёл валидацию",
                details={"errors": jsonable_encoder(validation_error.errors())},
            )
        ),
    )


async def _value_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=error_response_body(
            code=VALIDATION_ERROR_CODE,
            message=str(exc),
        ),
    )
