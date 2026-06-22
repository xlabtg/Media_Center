from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Annotated, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, Field, field_validator

from libs.shared import (
    IDEMPOTENCY_CONFLICT_CODE,
    VALIDATION_ERROR_CODE,
    AuditHash,
    BaseAppConfig,
    CorrelationId,
    IdempotencyKey,
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
    create_base_app,
    error_response_body,
    require_tenant_context,
)

from .contribution_events import record_contribution_event
from .payout_exporter import PayoutDistributionExport, build_payout_distribution_export
from .points_calculator import ContributionEventType, Platform, calculate_points
from .weight_engine import (
    COUNCIL_CAP_KV,
    MemberWeightOutput,
    NonNegativeFiniteFloat,
    WeightCalculationOutput,
    calculate_weights,
)

CONTRIBUTION_LEDGER_SERVICE_NAME = "contribution-ledger"

_PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
_SOURCE_TYPE_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
_IDEMPOTENCY_HEADER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"

Period = Annotated[str, Field(pattern=_PERIOD_PATTERN)]
SourceType = Annotated[str, Field(pattern=_SOURCE_TYPE_PATTERN)]
IdempotencyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        pattern=_IDEMPOTENCY_HEADER_PATTERN,
    ),
]


class ContributionCreateRequest(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    member_id: SubjectId
    event_type: ContributionEventType
    source_type: SourceType = "manual"
    source_ref: IdempotencyKey
    platform: Platform = Platform.DEFAULT
    reach: int = Field(default=0, ge=0)
    extra_reach: int = Field(default=0, ge=0)
    metadata: dict[str, JSONValue] = Field(default_factory=dict)
    occurred_at: datetime | None = None

    @field_validator("event_type", "platform", mode="before")
    @classmethod
    def _normalize_tokens(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("occurred_at")
    @classmethod
    def _normalize_optional_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _normalize_datetime(value)


class ContributionResponse(SharedBaseModel):
    contribution_id: IdempotencyKey
    tenant_id: TenantId
    member_id: SubjectId
    event_type: ContributionEventType
    source_type: SourceType
    source_ref: IdempotencyKey
    points_awarded: NonNegativeFiniteFloat
    audit_hash: AuditHash
    idempotency_key: IdempotencyKey
    occurred_at: datetime
    created_at: datetime


class WeightRecalculationRequest(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    period: Period
    avg_points_council: NonNegativeFiniteFloat | None = None
    cap_kv: float = Field(default=COUNCIL_CAP_KV, ge=0, le=1, allow_inf_nan=False)


class WeightMemberResponse(SharedBaseModel):
    member_id: SubjectId
    total_points: NonNegativeFiniteFloat
    avg_points_council: NonNegativeFiniteFloat
    kv_raw: NonNegativeFiniteFloat
    kv_capped: NonNegativeFiniteFloat
    payout_share: NonNegativeFiniteFloat


class WeightSnapshotResponse(SharedBaseModel):
    tenant_id: TenantId
    period: Period
    avg_points_council: NonNegativeFiniteFloat
    cap_kv: NonNegativeFiniteFloat
    total_kv_capped: NonNegativeFiniteFloat
    total_payout_share: NonNegativeFiniteFloat
    calculation_hash: AuditHash
    calculated_at: datetime
    members: tuple[WeightMemberResponse, ...]


@dataclass(frozen=True, slots=True)
class ContributionRecord:
    contribution_id: str
    tenant_id: str
    member_id: str
    event_type: str
    source_type: str
    source_ref: str
    points_awarded: float
    audit_hash: str
    idempotency_key: str
    request_hash: str
    metadata: dict[str, JSONValue]
    occurred_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class WeightSnapshot:
    tenant_id: str
    period: str
    result: WeightCalculationOutput
    calculation_hash: str
    calculated_at: datetime


@dataclass(slots=True)
class InMemoryContributionLedgerRepository:
    _contributions: list[ContributionRecord] = field(default_factory=list)
    _weight_snapshots: dict[tuple[str, str], WeightSnapshot] = field(
        default_factory=dict
    )
    _tenant_guard: TenantScopedRepository[ContributionRecord] = field(
        default_factory=lambda: TenantScopedRepository("contributions")
    )

    def get_existing_contribution(
        self,
        *,
        context: TenantContext,
        idempotency_key: str,
        request_hash: str,
    ) -> ContributionRecord | None:
        for record in self._contributions:
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

    def add_contribution(self, record: ContributionRecord) -> ContributionRecord:
        self._contributions.append(record)
        self._weight_snapshots.pop(
            (record.tenant_id, _period_from_datetime(record.occurred_at)),
            None,
        )
        return record

    def list_contributions_for_period(
        self,
        *,
        context: TenantContext,
        period: str,
    ) -> tuple[ContributionRecord, ...]:
        records = self._tenant_guard.list_for_tenant(self._contributions, context)
        return tuple(
            record
            for record in records
            if _period_from_datetime(record.occurred_at) == period
        )

    def get_weight_snapshot(
        self,
        *,
        context: TenantContext,
        period: str,
    ) -> WeightSnapshot | None:
        snapshot = self._weight_snapshots.get((context.tenant_id, period))
        if snapshot is None:
            return None
        if snapshot.tenant_id != context.tenant_id:
            raise AssertionError("tenant-scoped snapshot key invariant violated")
        return snapshot

    def save_weight_snapshot(self, snapshot: WeightSnapshot) -> WeightSnapshot:
        self._weight_snapshots[(snapshot.tenant_id, snapshot.period)] = snapshot
        return snapshot


@dataclass(slots=True)
class ContributionLedgerAPIState:
    repository: InMemoryContributionLedgerRepository = field(
        default_factory=InMemoryContributionLedgerRepository
    )
    publisher: InMemoryEventBus = field(default_factory=InMemoryEventBus)
    audit_sink: InMemoryAuditSink = field(default_factory=InMemoryAuditSink)


router = APIRouter(tags=["Contribution Ledger"])


def create_contribution_ledger_app(
    config: BaseAppConfig | ServiceTemplateConfig,
    *,
    repository: InMemoryContributionLedgerRepository | None = None,
    publisher: InMemoryEventBus | None = None,
    audit_sink: InMemoryAuditSink | None = None,
) -> FastAPI:
    resolved_audit_sink = audit_sink or InMemoryAuditSink()
    app = create_base_app(
        _base_app_config(config),
        audit_sink=resolved_audit_sink,
    )
    app.state.contribution_ledger_api = ContributionLedgerAPIState(
        repository=repository or InMemoryContributionLedgerRepository(),
        publisher=publisher or InMemoryEventBus(),
        audit_sink=resolved_audit_sink,
    )
    app.add_exception_handler(TenantCoreError, _tenant_core_error_handler)
    app.add_exception_handler(SharedError, _shared_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.include_router(router)
    return app


def _base_app_config(config: BaseAppConfig | ServiceTemplateConfig) -> BaseAppConfig:
    if isinstance(config, BaseAppConfig):
        if config.title is None:
            return replace(config, title="Media Center Contribution Ledger")

        return config

    return BaseAppConfig(
        service=config,
        title="Media Center Contribution Ledger",
    )


@router.post(
    "/contributions",
    response_model=ContributionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Зарегистрировать вклад",
)
async def register_contribution(
    payload: ContributionCreateRequest,
    idempotency_key: IdempotencyHeader,
    state: Annotated[ContributionLedgerAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> ContributionResponse:
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
    existing_record = state.repository.get_existing_contribution(
        context=context,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if existing_record is not None:
        return _contribution_response(existing_record)

    calculated = calculate_points(
        event_type=payload.event_type,
        platform=payload.platform,
        reach=payload.reach,
        extra_reach=payload.extra_reach,
    )
    occurred_at = payload.occurred_at or datetime.now(UTC)
    created_at = datetime.now(UTC)
    contribution_id = f"contribution-{uuid4()}"
    event_metadata = _event_metadata(payload)
    event_result = await record_contribution_event(
        publisher=state.publisher,
        contribution_id=contribution_id,
        tenant_id=context.tenant_id,
        member_id=payload.member_id,
        contribution_type=payload.event_type,
        points_awarded=calculated.final_points,
        metadata=event_metadata,
        occurred_at=occurred_at,
        correlation_id=_correlation_id(context),
    )
    record = state.repository.add_contribution(
        ContributionRecord(
            contribution_id=contribution_id,
            tenant_id=context.tenant_id,
            member_id=payload.member_id,
            event_type=event_result.contribution_type,
            source_type=payload.source_type,
            source_ref=payload.source_ref,
            points_awarded=event_result.points_awarded,
            audit_hash=event_result.audit_hash,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            metadata=payload.metadata,
            occurred_at=occurred_at,
            created_at=created_at,
        )
    )
    return _contribution_response(record)


@router.get(
    "/weights",
    response_model=WeightSnapshotResponse,
    summary="Получить веса Кв за период",
)
def get_weights(
    period: Annotated[str, Query(pattern=_PERIOD_PATTERN)],
    state: Annotated[ContributionLedgerAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
    avg_points_council: Annotated[float | None, Query(ge=0)] = None,
) -> WeightSnapshotResponse:
    if avg_points_council is None:
        existing = state.repository.get_weight_snapshot(context=context, period=period)
        if existing is not None:
            return _weight_snapshot_response(existing)

    snapshot = _calculate_weight_snapshot(
        repository=state.repository,
        context=context,
        period=period,
        avg_points_council=avg_points_council,
        cap_kv=COUNCIL_CAP_KV,
    )
    return _weight_snapshot_response(snapshot)


@router.post(
    "/weights/recalculate",
    response_model=WeightSnapshotResponse,
    summary="Пересчитать веса Кв за период",
)
def recalculate_weights(
    payload: WeightRecalculationRequest,
    _idempotency_key: IdempotencyHeader,
    state: Annotated[ContributionLedgerAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> WeightSnapshotResponse:
    snapshot = _calculate_weight_snapshot(
        repository=state.repository,
        context=context,
        period=payload.period,
        avg_points_council=payload.avg_points_council,
        cap_kv=payload.cap_kv,
    )
    state.repository.save_weight_snapshot(snapshot)
    return _weight_snapshot_response(snapshot)


@router.get(
    "/payout-distribution",
    response_model=PayoutDistributionExport,
    summary="Получить доли распределения для HITL",
)
def get_payout_distribution(
    period: Annotated[str, Query(pattern=_PERIOD_PATTERN)],
    state: Annotated[ContributionLedgerAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> PayoutDistributionExport:
    snapshot = state.repository.get_weight_snapshot(context=context, period=period)
    if snapshot is None:
        snapshot = _calculate_weight_snapshot(
            repository=state.repository,
            context=context,
            period=period,
            avg_points_council=None,
            cap_kv=COUNCIL_CAP_KV,
        )

    return build_payout_distribution_export(
        tenant_id=context.tenant_id,
        period=period,
        weights=snapshot.result,
        created_by=context.subject or "system",
    )


def _api_state(request: Request) -> ContributionLedgerAPIState:
    return cast(ContributionLedgerAPIState, request.app.state.contribution_ledger_api)


def _tenant_context() -> TenantContext:
    return require_tenant_context()


def _correlation_id(context: TenantContext) -> CorrelationId:
    return context.correlation_id or f"corr-{uuid4()}"


def _event_metadata(payload: ContributionCreateRequest) -> dict[str, JSONValue]:
    return {
        "source_type": payload.source_type,
        "source_ref": payload.source_ref,
        "platform": payload.platform.value,
        "reach": payload.reach,
        "extra_reach": payload.extra_reach,
        "metadata": payload.metadata,
    }


def _calculate_weight_snapshot(
    *,
    repository: InMemoryContributionLedgerRepository,
    context: TenantContext,
    period: str,
    avg_points_council: float | None,
    cap_kv: float,
) -> WeightSnapshot:
    records = repository.list_contributions_for_period(context=context, period=period)
    member_points = _member_points_from_records(records)
    resolved_average = _resolve_avg_points_council(
        avg_points_council,
        member_points,
    )
    result = calculate_weights(
        avg_points_council=resolved_average,
        members=[
            {"member_id": member_id, "points": total_points}
            for member_id, total_points in member_points
        ],
        cap_kv=cap_kv,
    )
    return WeightSnapshot(
        tenant_id=context.tenant_id,
        period=period,
        result=result,
        calculation_hash=_calculation_hash(
            tenant_id=context.tenant_id,
            period=period,
            result=result,
        ),
        calculated_at=datetime.now(UTC),
    )


def _member_points_from_records(
    records: tuple[ContributionRecord, ...],
) -> tuple[tuple[str, float], ...]:
    totals: dict[str, float] = {}
    for record in records:
        totals[record.member_id] = totals.get(record.member_id, 0.0) + float(
            record.points_awarded
        )

    return tuple(
        (member_id, round(total_points, 2))
        for member_id, total_points in sorted(totals.items())
    )


def _resolve_avg_points_council(
    explicit_average: float | None,
    member_points: tuple[tuple[str, float], ...],
) -> float:
    if explicit_average is not None:
        return explicit_average
    if not member_points:
        return 0.0
    return round(
        sum(total_points for _member_id, total_points in member_points)
        / len(member_points),
        2,
    )


def _calculation_hash(
    *,
    tenant_id: str,
    period: str,
    result: WeightCalculationOutput,
) -> str:
    return _hash_json_payload(
        {
            "tenant_id": tenant_id,
            "period": period,
            "avg_points_council": result.avg_points_council,
            "cap_kv": result.cap_kv,
            "members": [
                _member_weight_hash_payload(member) for member in result.members
            ],
        }
    )


def _member_weight_hash_payload(member: MemberWeightOutput) -> dict[str, JSONValue]:
    return {
        "member_id": member.member_id,
        "total_points": member.total_points,
        "avg_points_council": member.avg_points_council,
        "kv_raw": member.kv_raw,
        "kv_capped": member.kv_capped,
        "payout_share": member.payout_share,
    }


def _contribution_response(record: ContributionRecord) -> ContributionResponse:
    return ContributionResponse(
        contribution_id=record.contribution_id,
        tenant_id=record.tenant_id,
        member_id=record.member_id,
        event_type=ContributionEventType(record.event_type),
        source_type=record.source_type,
        source_ref=record.source_ref,
        points_awarded=record.points_awarded,
        audit_hash=record.audit_hash,
        idempotency_key=record.idempotency_key,
        occurred_at=record.occurred_at,
        created_at=record.created_at,
    )


def _weight_snapshot_response(snapshot: WeightSnapshot) -> WeightSnapshotResponse:
    return WeightSnapshotResponse(
        tenant_id=snapshot.tenant_id,
        period=snapshot.period,
        avg_points_council=snapshot.result.avg_points_council,
        cap_kv=snapshot.result.cap_kv,
        total_kv_capped=snapshot.result.total_kv_capped,
        total_payout_share=snapshot.result.total_payout_share,
        calculation_hash=snapshot.calculation_hash,
        calculated_at=snapshot.calculated_at,
        members=tuple(
            WeightMemberResponse(
                member_id=member.member_id,
                total_points=member.total_points,
                avg_points_council=member.avg_points_council,
                kv_raw=member.kv_raw,
                kv_capped=member.kv_capped,
                payout_share=member.payout_share,
            )
            for member in snapshot.result.members
        ),
    )


def _period_from_datetime(value: datetime) -> str:
    normalized = _normalize_datetime(value)
    return f"{normalized.year:04d}-{normalized.month:02d}"


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _hash_json_payload(payload: Mapping[str, JSONValue]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


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
                details={"errors": validation_error.errors()},
            )
        ),
    )
