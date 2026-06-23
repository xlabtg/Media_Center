from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from pydantic import Field

from libs.shared.errors import SharedError
from libs.shared.models import JSONValue, SharedBaseModel, TenantId
from libs.shared.tenant import TenantContext, TenantScopedRepository


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

    def list_periods(
        self,
        *,
        context: TenantContext,
    ) -> tuple[str, ...]:
        records = self._tenant_guard.list_for_tenant(self._events, context)
        return tuple(sorted({event.period for event in records}, reverse=True))


def build_analytics_kpi_response(
    *,
    tenant_id: str,
    period: str,
    events: Iterable[AnalyticsEventRecord],
) -> AnalyticsKPIResponse:
    metrics = _build_kpi_metrics(events)
    return AnalyticsKPIResponse(
        tenant_id=tenant_id,
        period=period,
        metrics=metrics,
        summary=_build_kpi_summary(metrics),
    )


def build_analytics_aggregates_response(
    *,
    tenant_id: str,
    period: str,
    events: Iterable[AnalyticsEventRecord],
) -> AnalyticsAggregatesResponse:
    return AnalyticsAggregatesResponse(
        tenant_id=tenant_id,
        period=period,
        categories=_build_category_aggregates(events),
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
