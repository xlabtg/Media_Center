from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi import status as http_status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, Field, field_validator

from libs.shared import (
    BOARD_ROLE,
    COUNCIL_ROLE,
    MEMBER_ASSOC_ROLE,
    MEMBER_FULL_ROLE,
    PRESIDIUM_ROLE,
    VALIDATION_ERROR_CODE,
    AccessPolicy,
    AuditHash,
    AuditLogger,
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
    create_service_app,
    error_response_body,
    require_access,
    require_tenant_context,
)
from libs.shared.events import EventEnvelope

ANALYTICS_ENGINE_SERVICE_NAME = "analytics-engine"
ANALYTICS_ENGINE_SOURCE = "analytics-engine"
ANALYTICS_ENGINE_SCHEMA_VERSION = "1.0"
ANALYTICS_EVENT_RECORDED_EVENT = "analytics.event_recorded"

_PERIOD_PATTERN = r"^\d{4}-((0[1-9]|1[0-2])|W(0[1-9]|[1-4][0-9]|5[0-3]))$"

ANALYTICS_EVENT_RECORD_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    BOARD_ROLE,
    MEMBER_FULL_ROLE,
    MEMBER_ASSOC_ROLE,
    action="analytics.event.record",
    resource_type="analytics_event",
)
ANALYTICS_READ_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    action="analytics.read",
    resource_type="analytics",
)


class AnalyticsCategory(StrEnum):
    PARTICIPATION = "participation"
    CONTENT = "content"
    ENGAGEMENT = "engagement"
    ACTIONS = "actions"


class AnalyticsEventType(StrEnum):
    MEMBER_ACTIVE = "member_active"
    MEMBER_JOINED = "member_joined"
    MATERIAL_PUBLISHED = "material_published"
    CONTENT_VIEWED = "content_viewed"
    READING_TIME_RECORDED = "reading_time_recorded"
    COMMENT_CREATED = "comment_created"
    TASK_COMPLETED = "task_completed"
    INITIATIVE_CREATED = "initiative_created"


class KPIStatus(StrEnum):
    BELOW_TARGET = "below_target"
    ON_TRACK = "on_track"
    ABOVE_TARGET = "above_target"


_EVENT_CATEGORIES: dict[AnalyticsEventType, AnalyticsCategory] = {
    AnalyticsEventType.MEMBER_ACTIVE: AnalyticsCategory.PARTICIPATION,
    AnalyticsEventType.MEMBER_JOINED: AnalyticsCategory.PARTICIPATION,
    AnalyticsEventType.MATERIAL_PUBLISHED: AnalyticsCategory.CONTENT,
    AnalyticsEventType.CONTENT_VIEWED: AnalyticsCategory.CONTENT,
    AnalyticsEventType.READING_TIME_RECORDED: AnalyticsCategory.CONTENT,
    AnalyticsEventType.COMMENT_CREATED: AnalyticsCategory.ENGAGEMENT,
    AnalyticsEventType.TASK_COMPLETED: AnalyticsCategory.ACTIONS,
    AnalyticsEventType.INITIATIVE_CREATED: AnalyticsCategory.ACTIONS,
}


@dataclass(frozen=True, slots=True)
class _KPIDefinition:
    key: str
    category: AnalyticsCategory
    label: str
    unit: str
    target_min: float | None
    target_max: float | None
    target_window: str


_KPI_DEFINITIONS: tuple[_KPIDefinition, ...] = (
    _KPIDefinition(
        key="active_members",
        category=AnalyticsCategory.PARTICIPATION,
        label="Активные члены Совета",
        unit="members",
        target_min=15,
        target_max=25,
        target_window="pilot",
    ),
    _KPIDefinition(
        key="new_members",
        category=AnalyticsCategory.PARTICIPATION,
        label="Новые участники",
        unit="members",
        target_min=3,
        target_max=5,
        target_window="month",
    ),
    _KPIDefinition(
        key="materials_published",
        category=AnalyticsCategory.CONTENT,
        label="Материалы",
        unit="items",
        target_min=20,
        target_max=30,
        target_window="week",
    ),
    _KPIDefinition(
        key="content_views",
        category=AnalyticsCategory.CONTENT,
        label="Просмотры",
        unit="views",
        target_min=10_000,
        target_max=None,
        target_window="week",
    ),
    _KPIDefinition(
        key="avg_reading_minutes",
        category=AnalyticsCategory.CONTENT,
        label="Среднее время чтения",
        unit="minutes",
        target_min=3,
        target_max=None,
        target_window="session",
    ),
    _KPIDefinition(
        key="comments",
        category=AnalyticsCategory.ENGAGEMENT,
        label="Комментарии",
        unit="items",
        target_min=50,
        target_max=None,
        target_window="week",
    ),
    _KPIDefinition(
        key="tasks_completed",
        category=AnalyticsCategory.ACTIONS,
        label="Задачи",
        unit="items",
        target_min=10,
        target_max=None,
        target_window="week",
    ),
    _KPIDefinition(
        key="initiatives_created",
        category=AnalyticsCategory.ACTIONS,
        label="Инициативы",
        unit="items",
        target_min=1,
        target_max=2,
        target_window="month",
    ),
)


class AnalyticsEventCreateRequest(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    event_id: IdempotencyKey | None = None
    type: AnalyticsEventType
    period: str = Field(pattern=_PERIOD_PATTERN)
    value: float = Field(default=1, ge=0, allow_inf_nan=False)
    sample_count: int | None = Field(default=None, ge=1)
    member_id: SubjectId | None = None
    occurred_at: datetime | None = None
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_type(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("occurred_at")
    @classmethod
    def _normalize_optional_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _normalize_datetime(value)


class AnalyticsEventResponse(SharedBaseModel):
    event_id: IdempotencyKey
    tenant_id: TenantId
    type: AnalyticsEventType
    category: AnalyticsCategory
    period: str
    value: float = Field(ge=0, allow_inf_nan=False)
    sample_count: int | None = Field(default=None, ge=1)
    member_hash: str | None = None
    audit_hash: AuditHash
    occurred_at: datetime


class KPIMetric(SharedBaseModel):
    key: str
    category: AnalyticsCategory
    label: str
    value: float = Field(ge=0, allow_inf_nan=False)
    unit: str
    target_min: float | None = None
    target_max: float | None = None
    target_window: str
    status: KPIStatus


class KPISummary(SharedBaseModel):
    metrics_total: int = Field(ge=0)
    metrics_on_track: int = Field(ge=0)
    metrics_below_target: int = Field(ge=0)
    metrics_above_target: int = Field(ge=0)


class AnalyticsKPIResponse(SharedBaseModel):
    tenant_id: TenantId
    period: str
    metrics: tuple[KPIMetric, ...]
    summary: KPISummary


class AnalyticsCategoryAggregate(SharedBaseModel):
    category: AnalyticsCategory
    event_count: int = Field(ge=0)
    unique_members: int = Field(ge=0)
    totals: dict[str, float] = Field(default_factory=dict)


class AnalyticsAggregatesResponse(SharedBaseModel):
    tenant_id: TenantId
    period: str
    categories: tuple[AnalyticsCategoryAggregate, ...]


@dataclass(frozen=True, slots=True)
class AnalyticsEventRecord:
    event_id: str
    tenant_id: str
    type: str
    category: str
    period: str
    value: float
    sample_count: int | None
    member_hash: str | None
    audit_hash: str
    correlation_id: str
    occurred_at: datetime
    metadata: dict[str, JSONValue]


@dataclass(slots=True)
class InMemoryAnalyticsRepository:
    _events: list[AnalyticsEventRecord] = field(default_factory=list)
    _tenant_guard: TenantScopedRepository[AnalyticsEventRecord] = field(
        default_factory=lambda: TenantScopedRepository("analytics_events")
    )

    def event_exists(self, *, tenant_id: str, event_id: str) -> bool:
        return any(
            event.tenant_id == tenant_id and event.event_id == event_id
            for event in self._events
        )

    def add_event(self, record: AnalyticsEventRecord) -> AnalyticsEventRecord:
        if self.event_exists(tenant_id=record.tenant_id, event_id=record.event_id):
            raise SharedError(
                status_code=409,
                error_code="analytics_event_conflict",
                message="Аналитическое событие с таким event_id уже существует",
            )

        self._events.append(record)
        return record

    def list_events(
        self,
        *,
        context: TenantContext,
        period: str,
    ) -> tuple[AnalyticsEventRecord, ...]:
        records = self._tenant_guard.list_for_tenant(self._events, context)
        filtered = (event for event in records if event.period == period)
        return tuple(
            sorted(
                filtered,
                key=lambda event: (event.occurred_at, event.event_id),
            )
        )


@dataclass(slots=True)
class AnalyticsEngineAPIState:
    repository: InMemoryAnalyticsRepository
    publisher: InMemoryEventBus
    audit_logger: AuditLogger
    audit_log_sink: InMemoryAuditLogSink
    tenant_audit_sink: InMemoryAuditSink


router = APIRouter(tags=["Analytics Engine"])


def create_analytics_engine_app(
    config: ServiceTemplateConfig,
    *,
    repository: InMemoryAnalyticsRepository | None = None,
    publisher: InMemoryEventBus | None = None,
    audit_log_sink: InMemoryAuditLogSink | None = None,
    tenant_audit_sink: InMemoryAuditSink | None = None,
) -> FastAPI:
    resolved_repository = repository or InMemoryAnalyticsRepository()
    resolved_publisher = publisher or InMemoryEventBus()
    resolved_audit_log_sink = audit_log_sink or InMemoryAuditLogSink()
    resolved_tenant_audit_sink = tenant_audit_sink or InMemoryAuditSink()
    audit_logger = AuditLogger(sink=resolved_audit_log_sink)
    app = create_service_app(
        config,
        title="Media Center Analytics Engine",
        audit_sink=resolved_tenant_audit_sink,
    )
    app.state.analytics_engine_api = AnalyticsEngineAPIState(
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
    "/analytics/events",
    response_model=AnalyticsEventResponse,
    status_code=http_status.HTTP_201_CREATED,
    summary="Записать нормализованное событие KPI",
)
async def record_analytics_event(
    payload: AnalyticsEventCreateRequest,
    state: Annotated[AnalyticsEngineAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> AnalyticsEventResponse:
    require_access(ANALYTICS_EVENT_RECORD_POLICY, context=context)
    event_id = payload.event_id or _new_id("analytics-event")
    if state.repository.event_exists(tenant_id=context.tenant_id, event_id=event_id):
        raise SharedError(
            status_code=409,
            error_code="analytics_event_conflict",
            message="Аналитическое событие с таким event_id уже существует",
            correlation_id=context.correlation_id,
        )

    occurred_at = payload.occurred_at or datetime.now(UTC)
    event_type = payload.type
    category = _EVENT_CATEGORIES[event_type]
    member_hash = _member_hash_from_payload(context=context, payload=payload)
    audit_record = state.audit_logger.record(
        event_type=ANALYTICS_EVENT_RECORDED_EVENT,
        tenant_id=context.tenant_id,
        metadata={
            "event_id": event_id,
            "type": event_type.value,
            "category": category.value,
            "period": payload.period,
            "value": payload.value,
            "sample_count": payload.sample_count,
            "member_hash": member_hash,
            "metadata": payload.metadata,
        },
        timestamp=occurred_at,
        correlation_id=_correlation_id(context),
        actor_hash=_actor_hash(context),
        source=ANALYTICS_ENGINE_SOURCE,
    )
    record = state.repository.add_event(
        AnalyticsEventRecord(
            event_id=event_id,
            tenant_id=context.tenant_id,
            type=event_type.value,
            category=category.value,
            period=payload.period,
            value=payload.value,
            sample_count=payload.sample_count,
            member_hash=member_hash,
            audit_hash=audit_record.audit_hash,
            correlation_id=_correlation_id(context),
            occurred_at=occurred_at,
            metadata=payload.metadata,
        )
    )
    await state.publisher.publish(_event_envelope(record))
    return _event_response(record)


@router.get(
    "/analytics/kpi",
    response_model=AnalyticsKPIResponse,
    summary="Получить KPI tenant за период",
)
def get_analytics_kpi(
    state: Annotated[AnalyticsEngineAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
    period: Annotated[str, Query(pattern=_PERIOD_PATTERN)],
) -> AnalyticsKPIResponse:
    require_access(ANALYTICS_READ_POLICY, context=context)
    metrics = _build_kpi_metrics(
        state.repository.list_events(context=context, period=period)
    )
    return AnalyticsKPIResponse(
        tenant_id=context.tenant_id,
        period=period,
        metrics=metrics,
        summary=_build_kpi_summary(metrics),
    )


@router.get(
    "/analytics/aggregates",
    response_model=AnalyticsAggregatesResponse,
    summary="Получить агрегаты активности, контента и вовлечённости tenant",
)
def get_analytics_aggregates(
    state: Annotated[AnalyticsEngineAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
    period: Annotated[str, Query(pattern=_PERIOD_PATTERN)],
) -> AnalyticsAggregatesResponse:
    require_access(ANALYTICS_READ_POLICY, context=context)
    return AnalyticsAggregatesResponse(
        tenant_id=context.tenant_id,
        period=period,
        categories=_build_category_aggregates(
            state.repository.list_events(context=context, period=period)
        ),
    )


def subject_ref_hash(*, tenant_id: str, subject_id: str) -> str:
    return "sha256:" + hashlib.sha256(f"{tenant_id}:{subject_id}".encode()).hexdigest()


def _api_state(request: Request) -> AnalyticsEngineAPIState:
    return cast(AnalyticsEngineAPIState, request.app.state.analytics_engine_api)


def _tenant_context() -> TenantContext:
    return require_tenant_context()


def _member_hash_from_payload(
    *,
    context: TenantContext,
    payload: AnalyticsEventCreateRequest,
) -> str | None:
    member_id = payload.member_id
    if member_id is None and payload.type == AnalyticsEventType.MEMBER_ACTIVE:
        member_id = context.subject
    if member_id is None:
        return None

    return subject_ref_hash(tenant_id=context.tenant_id, subject_id=member_id)


def _actor_hash(context: TenantContext) -> str | None:
    if context.subject is None:
        return None
    return subject_ref_hash(tenant_id=context.tenant_id, subject_id=context.subject)


def _correlation_id(context: TenantContext) -> CorrelationId:
    return context.correlation_id or f"corr-{uuid4()}"


def _event_response(record: AnalyticsEventRecord) -> AnalyticsEventResponse:
    return AnalyticsEventResponse(
        event_id=record.event_id,
        tenant_id=record.tenant_id,
        type=AnalyticsEventType(record.type),
        category=AnalyticsCategory(record.category),
        period=record.period,
        value=record.value,
        sample_count=record.sample_count,
        member_hash=record.member_hash,
        audit_hash=record.audit_hash,
        occurred_at=record.occurred_at,
    )


def _event_envelope(record: AnalyticsEventRecord) -> EventEnvelope:
    payload: dict[str, JSONValue] = {
        "event_id": record.event_id,
        "type": record.type,
        "category": record.category,
        "period": record.period,
        "value": record.value,
        "sample_count": record.sample_count,
        "member_hash": record.member_hash,
        "audit_hash": record.audit_hash,
    }
    return EventEnvelope(
        event_id=_new_id("evt-analytics-event-recorded"),
        type=ANALYTICS_EVENT_RECORDED_EVENT,
        schema_version=ANALYTICS_ENGINE_SCHEMA_VERSION,
        tenant_id=record.tenant_id,
        source=ANALYTICS_ENGINE_SOURCE,
        correlation_id=record.correlation_id,
        occurred_at=record.occurred_at,
        payload=payload,
    )


def _build_kpi_metrics(
    events: Iterable[AnalyticsEventRecord],
) -> tuple[KPIMetric, ...]:
    event_list = tuple(events)
    values = {
        "active_members": float(
            len(
                {
                    event.member_hash
                    for event in event_list
                    if event.type == AnalyticsEventType.MEMBER_ACTIVE.value
                    and event.member_hash is not None
                }
            )
            + _sum_values_without_member_hash(
                event_list,
                AnalyticsEventType.MEMBER_ACTIVE,
            )
        ),
        "new_members": _sum_values(event_list, AnalyticsEventType.MEMBER_JOINED),
        "materials_published": _sum_values(
            event_list,
            AnalyticsEventType.MATERIAL_PUBLISHED,
        ),
        "content_views": _sum_values(event_list, AnalyticsEventType.CONTENT_VIEWED),
        "avg_reading_minutes": _average_reading_minutes(event_list),
        "comments": _sum_values(event_list, AnalyticsEventType.COMMENT_CREATED),
        "tasks_completed": _sum_values(event_list, AnalyticsEventType.TASK_COMPLETED),
        "initiatives_created": _sum_values(
            event_list,
            AnalyticsEventType.INITIATIVE_CREATED,
        ),
    }
    return tuple(
        KPIMetric(
            key=definition.key,
            category=definition.category,
            label=definition.label,
            value=_round_metric(values[definition.key]),
            unit=definition.unit,
            target_min=definition.target_min,
            target_max=definition.target_max,
            target_window=definition.target_window,
            status=_status_for_value(
                values[definition.key],
                target_min=definition.target_min,
                target_max=definition.target_max,
            ),
        )
        for definition in _KPI_DEFINITIONS
    )


def _sum_values(
    events: Iterable[AnalyticsEventRecord],
    event_type: AnalyticsEventType,
) -> float:
    return sum(event.value for event in events if event.type == event_type.value)


def _sum_values_without_member_hash(
    events: Iterable[AnalyticsEventRecord],
    event_type: AnalyticsEventType,
) -> float:
    return sum(
        event.value
        for event in events
        if event.type == event_type.value and event.member_hash is None
    )


def _average_reading_minutes(events: Iterable[AnalyticsEventRecord]) -> float:
    reading_events = tuple(
        event
        for event in events
        if event.type == AnalyticsEventType.READING_TIME_RECORDED.value
    )
    sample_count = sum(event.sample_count or 1 for event in reading_events)
    if sample_count == 0:
        return 0.0

    total_seconds = sum(event.value for event in reading_events)
    return total_seconds / sample_count / 60


def _status_for_value(
    value: float,
    *,
    target_min: float | None,
    target_max: float | None,
) -> KPIStatus:
    if target_min is not None and value < target_min:
        return KPIStatus.BELOW_TARGET
    if target_max is not None and value > target_max:
        return KPIStatus.ABOVE_TARGET
    return KPIStatus.ON_TRACK


def _build_kpi_summary(metrics: Iterable[KPIMetric]) -> KPISummary:
    metric_list = tuple(metrics)
    return KPISummary(
        metrics_total=len(metric_list),
        metrics_on_track=sum(
            1 for metric in metric_list if metric.status == KPIStatus.ON_TRACK
        ),
        metrics_below_target=sum(
            1 for metric in metric_list if metric.status == KPIStatus.BELOW_TARGET
        ),
        metrics_above_target=sum(
            1 for metric in metric_list if metric.status == KPIStatus.ABOVE_TARGET
        ),
    )


def _build_category_aggregates(
    events: Iterable[AnalyticsEventRecord],
) -> tuple[AnalyticsCategoryAggregate, ...]:
    event_list = tuple(events)
    aggregates: list[AnalyticsCategoryAggregate] = []
    for category in AnalyticsCategory:
        category_events = tuple(
            event for event in event_list if event.category == category.value
        )
        totals: dict[str, float] = {}
        for event in category_events:
            totals[event.type] = totals.get(event.type, 0.0) + event.value
        aggregates.append(
            AnalyticsCategoryAggregate(
                category=category,
                event_count=len(category_events),
                unique_members=len(
                    {
                        event.member_hash
                        for event in category_events
                        if event.member_hash is not None
                    }
                ),
                totals={
                    metric_type: _round_metric(value)
                    for metric_type, value in sorted(totals.items())
                },
            )
        )

    return tuple(aggregates)


def _round_metric(value: float) -> float:
    return round(value, 4)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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
