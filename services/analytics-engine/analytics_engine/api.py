from __future__ import annotations

import hashlib
import re
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
PILOT_TELEMETRY_BATCH_COLLECTED_EVENT = "analytics.pilot_batch_collected"
PILOT_USAGE_RECORDED_EVENT = "analytics.pilot_usage_recorded"
PILOT_INCIDENT_RECORDED_EVENT = "analytics.pilot_incident_recorded"
RL_KPI_ITERATION_CREATED_EVENT = "analytics.rl_kpi_iteration_created"
RL_KPI_COUNCIL_DECISION_RECORDED_EVENT = "analytics.rl_kpi_council_decision_recorded"
RL_KPI_EFFECT_MEASURED_EVENT = "analytics.rl_kpi_effect_measured"

_PERIOD_PATTERN = r"^\d{4}-((0[1-9]|1[0-2])|W(0[1-9]|[1-4][0-9]|5[0-3]))$"
_PILOT_SOURCE_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"
_PILOT_SIGNAL_PATTERN = r"^[a-z][a-z0-9_:-]{0,127}$"
_PILOT_UNIT_PATTERN = r"^[A-Za-z][A-Za-z0-9_/%-]{0,31}$"
_PERIOD_RE = re.compile(_PERIOD_PATTERN)
_RL_KPI_MIN_WINDOW_DAYS = 7
_RL_KPI_MAX_WINDOW_DAYS = 30

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
PILOT_TELEMETRY_COLLECT_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    BOARD_ROLE,
    action="analytics.pilot.collect",
    resource_type="pilot_telemetry",
)
PILOT_COUNCIL_REPORT_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    action="analytics.pilot.report",
    resource_type="pilot_report",
)
RL_KPI_CREATE_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    BOARD_ROLE,
    action="analytics.rl_kpi.create",
    resource_type="rl_kpi_iteration",
)
RL_KPI_READ_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    action="analytics.rl_kpi.read",
    resource_type="rl_kpi_iteration",
)
RL_KPI_APPROVAL_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    action="analytics.rl_kpi.approve",
    resource_type="rl_kpi_iteration",
)
RL_KPI_EFFECT_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    BOARD_ROLE,
    action="analytics.rl_kpi.measure_effect",
    resource_type="rl_kpi_iteration",
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


class PilotIncidentSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PilotIncidentStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class RLKPIIterationStatus(StrEnum):
    AWAITING_COUNCIL_APPROVAL = "awaiting_council_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EFFECT_MEASURED = "effect_measured"
    ROLLBACK_REQUIRED = "rollback_required"
    MONITORING_ONLY = "monitoring_only"


class RLKPIProposalStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"


class RLKPIEffectStatus(StrEnum):
    PENDING = "pending"
    IMPROVED = "improved"
    DEGRADED = "degraded"
    STABLE = "stable"


class RLKPICouncilDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class RLKPIExpectedDirection(StrEnum):
    INCREASE = "increase"
    DECREASE = "decrease"


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


class PilotTelemetryKPIRequest(SharedBaseModel):
    active_members: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    new_members: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    materials_published: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    content_views: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    reading_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    reading_sessions: int | None = Field(default=None, ge=1)
    comments: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    tasks_completed: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    initiatives_created: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )


class PilotUsageTelemetryCreate(SharedBaseModel):
    signal_id: IdempotencyKey | None = None
    name: str = Field(pattern=_PILOT_SIGNAL_PATTERN)
    value: float = Field(ge=0, allow_inf_nan=False)
    unit: str = Field(pattern=_PILOT_UNIT_PATTERN)
    occurred_at: datetime | None = None
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("occurred_at")
    @classmethod
    def _normalize_optional_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _normalize_datetime(value)


class PilotIncidentCreate(SharedBaseModel):
    incident_id: IdempotencyKey | None = None
    severity: PilotIncidentSeverity
    status: PilotIncidentStatus
    title: str = Field(min_length=1, max_length=256)
    impact: str = Field(min_length=1, max_length=512)
    occurred_at: datetime | None = None
    resolved_at: datetime | None = None
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("severity", "status", mode="before")
    @classmethod
    def _normalize_enum(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("occurred_at", "resolved_at")
    @classmethod
    def _normalize_optional_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _normalize_datetime(value)


class PilotTelemetryCollectionRequest(SharedBaseModel):
    batch_id: IdempotencyKey | None = None
    source: str = Field(pattern=_PILOT_SOURCE_PATTERN)
    period: str = Field(pattern=_PERIOD_PATTERN)
    kpi: PilotTelemetryKPIRequest = Field(default_factory=PilotTelemetryKPIRequest)
    usage: tuple[PilotUsageTelemetryCreate, ...] = Field(default_factory=tuple)
    incidents: tuple[PilotIncidentCreate, ...] = Field(default_factory=tuple)
    collected_at: datetime | None = None
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("source", mode="before")
    @classmethod
    def _normalize_source(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("collected_at")
    @classmethod
    def _normalize_optional_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _normalize_datetime(value)


class PilotTelemetryCollectionResponse(SharedBaseModel):
    batch_id: IdempotencyKey
    tenant_id: TenantId
    period: str
    source: str
    kpi_events_recorded: int = Field(ge=0)
    usage_signals_recorded: int = Field(ge=0)
    incidents_recorded: int = Field(ge=0)


class PilotUsageMetricSummary(SharedBaseModel):
    name: str = Field(pattern=_PILOT_SIGNAL_PATTERN)
    unit: str = Field(pattern=_PILOT_UNIT_PATTERN)
    value: float = Field(ge=0, allow_inf_nan=False)
    signal_count: int = Field(ge=0)
    sources: tuple[str, ...]


class PilotUsageTelemetrySummary(SharedBaseModel):
    signals_total: int = Field(ge=0)
    metrics: tuple[PilotUsageMetricSummary, ...]


class PilotIncidentReportItem(SharedBaseModel):
    incident_id: IdempotencyKey
    severity: PilotIncidentSeverity
    status: PilotIncidentStatus
    title: str
    impact: str
    occurred_at: datetime
    resolved_at: datetime | None = None


class PilotIncidentSummary(SharedBaseModel):
    incidents_total: int = Field(ge=0)
    open_incidents: int = Field(ge=0)
    resolved_incidents: int = Field(ge=0)
    severity_counts: dict[str, int] = Field(default_factory=dict)
    latest: tuple[PilotIncidentReportItem, ...] = Field(default_factory=tuple)


class PilotFeedbackLoopSummary(SharedBaseModel):
    status: str
    signals: tuple[str, ...]


class PilotCouncilReportResponse(SharedBaseModel):
    tenant_id: TenantId
    period: str
    report_frequency: str
    recipients: tuple[str, ...]
    kpi: AnalyticsKPIResponse
    aggregates: AnalyticsAggregatesResponse
    usage: PilotUsageTelemetrySummary
    incidents: PilotIncidentSummary
    feedback_loop: PilotFeedbackLoopSummary
    generated_at: datetime


class RLKPIIterationCreateRequest(SharedBaseModel):
    iteration_id: IdempotencyKey | None = None
    periods: tuple[str, ...] = Field(min_length=1, max_length=5)
    window_days: int = Field(ge=_RL_KPI_MIN_WINDOW_DAYS, le=_RL_KPI_MAX_WINDOW_DAYS)
    policy_revision: IdempotencyKey
    started_at: datetime | None = None
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("periods")
    @classmethod
    def _validate_periods(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("periods не должен содержать дубликаты")
        for period in value:
            _validate_period(period)
        return value

    @field_validator("started_at")
    @classmethod
    def _normalize_optional_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _normalize_datetime(value)


class RLKPICouncilApprovalRequest(SharedBaseModel):
    decision: RLKPICouncilDecision
    decided_at: datetime | None = None
    decision_ref: str = Field(min_length=1, max_length=256)
    comment: str | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("decision", mode="before")
    @classmethod
    def _normalize_decision(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("decided_at")
    @classmethod
    def _normalize_optional_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _normalize_datetime(value)


class RLKPIEffectMeasurementRequest(SharedBaseModel):
    evaluation_period: str
    measured_at: datetime | None = None
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("evaluation_period")
    @classmethod
    def _validate_evaluation_period(cls, value: str) -> str:
        return _validate_period(value)

    @field_validator("measured_at")
    @classmethod
    def _normalize_optional_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _normalize_datetime(value)


class RLKPIEffectMeasurement(SharedBaseModel):
    evaluation_period: str
    measured_at: datetime
    baseline_value: float = Field(ge=0, allow_inf_nan=False)
    evaluation_value: float = Field(ge=0, allow_inf_nan=False)
    absolute_delta: float = Field(allow_inf_nan=False)
    relative_delta: float | None = Field(default=None, allow_inf_nan=False)
    status: RLKPIEffectStatus
    rollback_recommended: bool


class RLKPIOptimizationProposal(SharedBaseModel):
    proposal_id: IdempotencyKey
    metric_key: str = Field(min_length=1, max_length=128)
    category: AnalyticsCategory
    status: RLKPIProposalStatus
    policy_key: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=128)
    rationale_code: str = Field(min_length=1, max_length=128)
    baseline_value: float = Field(ge=0, allow_inf_nan=False)
    latest_value: float = Field(ge=0, allow_inf_nan=False)
    target_min: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    target_max: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    expected_direction: RLKPIExpectedDirection
    expected_lift: float = Field(ge=0, allow_inf_nan=False)
    xai_summary: str = Field(min_length=1, max_length=512)
    requires_council_approval: bool = True
    effect: RLKPIEffectMeasurement | None = None


class RLKPICouncilControl(SharedBaseModel):
    required_role: str
    approval_status: str
    approved_by: SubjectId | None = None
    approved_at: datetime | None = None
    decision_ref: str | None = None


class RLKPIMonitoringSummary(SharedBaseModel):
    baseline_period: str
    evaluation_period: str | None = None
    effect_status: RLKPIEffectStatus
    metrics: tuple[str, ...] = Field(default_factory=tuple)


class RLKPIIterationResponse(SharedBaseModel):
    iteration_id: IdempotencyKey
    tenant_id: TenantId
    periods: tuple[str, ...]
    window_days: int = Field(ge=_RL_KPI_MIN_WINDOW_DAYS, le=_RL_KPI_MAX_WINDOW_DAYS)
    policy_revision: IdempotencyKey
    status: RLKPIIterationStatus
    proposal_count: int = Field(ge=0)
    proposals: tuple[RLKPIOptimizationProposal, ...]
    council_control: RLKPICouncilControl
    monitoring: RLKPIMonitoringSummary
    audit_hash: AuditHash
    started_at: datetime
    updated_at: datetime


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


@dataclass(frozen=True, slots=True)
class PilotUsageTelemetryRecord:
    signal_id: str
    tenant_id: str
    source: str
    period: str
    name: str
    value: float
    unit: str
    audit_hash: str
    correlation_id: str
    occurred_at: datetime
    metadata: dict[str, JSONValue]


@dataclass(frozen=True, slots=True)
class PilotIncidentRecord:
    incident_id: str
    tenant_id: str
    source: str
    period: str
    severity: str
    status: str
    title: str
    impact: str
    audit_hash: str
    correlation_id: str
    occurred_at: datetime
    resolved_at: datetime | None
    metadata: dict[str, JSONValue]


@dataclass(frozen=True, slots=True)
class PilotTelemetryBatchRecord:
    batch_id: str
    tenant_id: str
    source: str
    period: str
    kpi_event_ids: tuple[str, ...]
    usage_signal_ids: tuple[str, ...]
    incident_ids: tuple[str, ...]
    audit_hash: str
    correlation_id: str
    collected_at: datetime
    metadata: dict[str, JSONValue]


@dataclass(frozen=True, slots=True)
class RLKPIIterationRecord:
    iteration_id: str
    tenant_id: str
    periods: tuple[str, ...]
    window_days: int
    policy_revision: str
    status: RLKPIIterationStatus
    proposals: tuple[RLKPIOptimizationProposal, ...]
    council_control: RLKPICouncilControl
    monitoring: RLKPIMonitoringSummary
    audit_hash: str
    correlation_id: str
    started_at: datetime
    updated_at: datetime
    metadata: dict[str, JSONValue]


@dataclass(slots=True)
class InMemoryAnalyticsRepository:
    _events: list[AnalyticsEventRecord] = field(default_factory=list)
    _pilot_batches: dict[tuple[str, str], PilotTelemetryBatchRecord] = field(
        default_factory=dict
    )
    _usage_signals: list[PilotUsageTelemetryRecord] = field(default_factory=list)
    _incidents: list[PilotIncidentRecord] = field(default_factory=list)
    _rl_kpi_iterations: dict[tuple[str, str], RLKPIIterationRecord] = field(
        default_factory=dict
    )
    _tenant_guard: TenantScopedRepository[AnalyticsEventRecord] = field(
        default_factory=lambda: TenantScopedRepository("analytics_events")
    )
    _usage_guard: TenantScopedRepository[PilotUsageTelemetryRecord] = field(
        default_factory=lambda: TenantScopedRepository("pilot_usage_telemetry")
    )
    _incident_guard: TenantScopedRepository[PilotIncidentRecord] = field(
        default_factory=lambda: TenantScopedRepository("pilot_incidents")
    )
    _rl_kpi_guard: TenantScopedRepository[RLKPIIterationRecord] = field(
        default_factory=lambda: TenantScopedRepository("rl_kpi_iterations")
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

    def pilot_batch_exists(self, *, tenant_id: str, batch_id: str) -> bool:
        return (tenant_id, batch_id) in self._pilot_batches

    def add_pilot_batch(
        self,
        record: PilotTelemetryBatchRecord,
    ) -> PilotTelemetryBatchRecord:
        key = (record.tenant_id, record.batch_id)
        if key in self._pilot_batches:
            raise SharedError(
                status_code=409,
                error_code="pilot_telemetry_batch_conflict",
                message="Пакет телеметрии пилота с таким batch_id уже существует",
            )

        self._pilot_batches[key] = record
        return record

    def rl_kpi_iteration_exists(
        self,
        *,
        tenant_id: str,
        iteration_id: str,
    ) -> bool:
        return (tenant_id, iteration_id) in self._rl_kpi_iterations

    def add_rl_kpi_iteration(
        self,
        record: RLKPIIterationRecord,
    ) -> RLKPIIterationRecord:
        key = (record.tenant_id, record.iteration_id)
        if key in self._rl_kpi_iterations:
            raise SharedError(
                status_code=409,
                error_code="rl_kpi_iteration_conflict",
                message="RL-KPI итерация с таким iteration_id уже существует",
            )

        self._rl_kpi_iterations[key] = record
        return record

    def save_rl_kpi_iteration(
        self,
        record: RLKPIIterationRecord,
    ) -> RLKPIIterationRecord:
        self._rl_kpi_iterations[(record.tenant_id, record.iteration_id)] = record
        return record

    def get_rl_kpi_iteration(
        self,
        *,
        context: TenantContext,
        iteration_id: str,
    ) -> RLKPIIterationRecord:
        records = self._rl_kpi_guard.list_for_tenant(
            self._rl_kpi_iterations.values(),
            context,
        )
        for record in records:
            if record.iteration_id == iteration_id:
                return record

        raise SharedError(
            status_code=404,
            error_code="rl_kpi_iteration_not_found",
            message="RL-KPI итерация не найдена",
            correlation_id=context.correlation_id,
        )

    def add_usage_signal(
        self,
        record: PilotUsageTelemetryRecord,
    ) -> PilotUsageTelemetryRecord:
        if any(
            item.tenant_id == record.tenant_id and item.signal_id == record.signal_id
            for item in self._usage_signals
        ):
            raise SharedError(
                status_code=409,
                error_code="pilot_usage_signal_conflict",
                message="Usage telemetry signal с таким signal_id уже существует",
            )

        self._usage_signals.append(record)
        return record

    def add_incident(self, record: PilotIncidentRecord) -> PilotIncidentRecord:
        if any(
            item.tenant_id == record.tenant_id
            and item.incident_id == record.incident_id
            for item in self._incidents
        ):
            raise SharedError(
                status_code=409,
                error_code="pilot_incident_conflict",
                message="Инцидент пилота с таким incident_id уже существует",
            )

        self._incidents.append(record)
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

    def list_usage_signals(
        self,
        *,
        context: TenantContext,
        period: str,
    ) -> tuple[PilotUsageTelemetryRecord, ...]:
        records = self._usage_guard.list_for_tenant(self._usage_signals, context)
        filtered = (signal for signal in records if signal.period == period)
        return tuple(
            sorted(filtered, key=lambda signal: (signal.name, signal.signal_id))
        )

    def list_incidents(
        self,
        *,
        context: TenantContext,
        period: str,
    ) -> tuple[PilotIncidentRecord, ...]:
        records = self._incident_guard.list_for_tenant(self._incidents, context)
        filtered = (incident for incident in records if incident.period == period)
        return tuple(
            sorted(
                filtered,
                key=lambda incident: (incident.occurred_at, incident.incident_id),
                reverse=True,
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
    record = _add_analytics_event(
        state=state,
        context=context,
        event_id=event_id,
        event_type=payload.type,
        period=payload.period,
        value=payload.value,
        sample_count=payload.sample_count,
        member_hash=_member_hash_from_payload(context=context, payload=payload),
        occurred_at=payload.occurred_at or datetime.now(UTC),
        metadata=payload.metadata,
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
    return build_analytics_kpi_response(
        tenant_id=context.tenant_id,
        period=period,
        events=state.repository.list_events(context=context, period=period),
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
    return build_analytics_aggregates_response(
        tenant_id=context.tenant_id,
        period=period,
        events=state.repository.list_events(context=context, period=period),
    )


@router.post(
    "/analytics/pilot/telemetry/collect",
    response_model=PilotTelemetryCollectionResponse,
    status_code=http_status.HTTP_201_CREATED,
    summary="Собрать batch KPI и телеметрии пилота",
)
async def collect_pilot_telemetry(
    payload: PilotTelemetryCollectionRequest,
    state: Annotated[AnalyticsEngineAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> PilotTelemetryCollectionResponse:
    require_access(PILOT_TELEMETRY_COLLECT_POLICY, context=context)
    batch_id = payload.batch_id or _new_id("pilot-telemetry-batch")
    if state.repository.pilot_batch_exists(
        tenant_id=context.tenant_id,
        batch_id=batch_id,
    ):
        raise SharedError(
            status_code=409,
            error_code="pilot_telemetry_batch_conflict",
            message="Пакет телеметрии пилота с таким batch_id уже существует",
            correlation_id=context.correlation_id,
        )

    collected_at = payload.collected_at or datetime.now(UTC)
    kpi_records: list[AnalyticsEventRecord] = []
    for event_type, value, sample_count in _pilot_kpi_event_specs(payload.kpi):
        record = _add_analytics_event(
            state=state,
            context=context,
            event_id=f"{batch_id}:{event_type.value}",
            event_type=event_type,
            period=payload.period,
            value=value,
            sample_count=sample_count,
            member_hash=None,
            occurred_at=collected_at,
            metadata={
                "batch_id": batch_id,
                "source": payload.source,
                "collector": "pilot_telemetry",
            },
        )
        await state.publisher.publish(_event_envelope(record))
        kpi_records.append(record)

    usage_records = tuple(
        _add_pilot_usage_signal(
            state=state,
            context=context,
            batch_id=batch_id,
            source=payload.source,
            period=payload.period,
            payload=usage,
            index=index,
            collected_at=collected_at,
        )
        for index, usage in enumerate(payload.usage, start=1)
    )
    incident_records = tuple(
        _add_pilot_incident(
            state=state,
            context=context,
            batch_id=batch_id,
            source=payload.source,
            period=payload.period,
            payload=incident,
            index=index,
            collected_at=collected_at,
        )
        for index, incident in enumerate(payload.incidents, start=1)
    )
    batch_audit_record = state.audit_logger.record(
        event_type=PILOT_TELEMETRY_BATCH_COLLECTED_EVENT,
        tenant_id=context.tenant_id,
        metadata={
            "batch_id": batch_id,
            "source": payload.source,
            "period": payload.period,
            "kpi_events_recorded": len(kpi_records),
            "usage_signals_recorded": len(usage_records),
            "incidents_recorded": len(incident_records),
            "metadata": payload.metadata,
        },
        timestamp=collected_at,
        correlation_id=_correlation_id(context),
        actor_hash=_actor_hash(context),
        source=payload.source,
    )
    state.repository.add_pilot_batch(
        PilotTelemetryBatchRecord(
            batch_id=batch_id,
            tenant_id=context.tenant_id,
            source=payload.source,
            period=payload.period,
            kpi_event_ids=tuple(record.event_id for record in kpi_records),
            usage_signal_ids=tuple(record.signal_id for record in usage_records),
            incident_ids=tuple(record.incident_id for record in incident_records),
            audit_hash=batch_audit_record.audit_hash,
            correlation_id=_correlation_id(context),
            collected_at=collected_at,
            metadata=payload.metadata,
        )
    )
    return PilotTelemetryCollectionResponse(
        batch_id=batch_id,
        tenant_id=context.tenant_id,
        period=payload.period,
        source=payload.source,
        kpi_events_recorded=len(kpi_records),
        usage_signals_recorded=len(usage_records),
        incidents_recorded=len(incident_records),
    )


@router.get(
    "/analytics/pilot/reports",
    response_model=PilotCouncilReportResponse,
    summary="Получить регулярный отчёт пилота для Совета",
)
def get_pilot_council_report(
    state: Annotated[AnalyticsEngineAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
    period: Annotated[str, Query(pattern=_PERIOD_PATTERN)],
) -> PilotCouncilReportResponse:
    require_access(PILOT_COUNCIL_REPORT_POLICY, context=context)
    events = state.repository.list_events(context=context, period=period)
    kpi = build_analytics_kpi_response(
        tenant_id=context.tenant_id,
        period=period,
        events=events,
    )
    aggregates = build_analytics_aggregates_response(
        tenant_id=context.tenant_id,
        period=period,
        events=events,
    )
    usage = _build_pilot_usage_summary(
        state.repository.list_usage_signals(context=context, period=period)
    )
    incidents = _build_pilot_incident_summary(
        state.repository.list_incidents(context=context, period=period)
    )
    return PilotCouncilReportResponse(
        tenant_id=context.tenant_id,
        period=period,
        report_frequency=_report_frequency(period),
        recipients=(COUNCIL_ROLE,),
        kpi=kpi,
        aggregates=aggregates,
        usage=usage,
        incidents=incidents,
        feedback_loop=_build_pilot_feedback_loop(
            kpi=kpi,
            incidents=incidents,
        ),
        generated_at=datetime.now(UTC),
    )


@router.post(
    "/analytics/rl-kpi/iterations",
    response_model=RLKPIIterationResponse,
    status_code=http_status.HTTP_201_CREATED,
    summary="Создать supervised RL-KPI итерацию по KPI за окно 7-30 дней",
)
async def create_rl_kpi_iteration(
    payload: RLKPIIterationCreateRequest,
    state: Annotated[AnalyticsEngineAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> RLKPIIterationResponse:
    require_access(RL_KPI_CREATE_POLICY, context=context)
    iteration_id = payload.iteration_id or _new_id("rl-kpi-iteration")
    if state.repository.rl_kpi_iteration_exists(
        tenant_id=context.tenant_id,
        iteration_id=iteration_id,
    ):
        raise SharedError(
            status_code=409,
            error_code="rl_kpi_iteration_conflict",
            message="RL-KPI итерация с таким iteration_id уже существует",
            correlation_id=context.correlation_id,
        )

    started_at = payload.started_at or datetime.now(UTC)
    period_kpis = tuple(
        build_analytics_kpi_response(
            tenant_id=context.tenant_id,
            period=period,
            events=state.repository.list_events(context=context, period=period),
        )
        for period in payload.periods
    )
    proposals = _build_rl_kpi_proposals(
        iteration_id=iteration_id,
        period_kpis=period_kpis,
        window_days=payload.window_days,
    )
    status = (
        RLKPIIterationStatus.AWAITING_COUNCIL_APPROVAL
        if proposals
        else RLKPIIterationStatus.MONITORING_ONLY
    )
    monitoring = RLKPIMonitoringSummary(
        baseline_period=payload.periods[-1],
        effect_status=RLKPIEffectStatus.PENDING,
        metrics=tuple(proposal.metric_key for proposal in proposals),
    )
    council_control = RLKPICouncilControl(
        required_role=COUNCIL_ROLE,
        approval_status="pending" if proposals else "not_required",
    )
    audit_record = state.audit_logger.record(
        event_type=RL_KPI_ITERATION_CREATED_EVENT,
        tenant_id=context.tenant_id,
        metadata={
            "iteration_id": iteration_id,
            "periods": list(payload.periods),
            "window_days": payload.window_days,
            "policy_revision": payload.policy_revision,
            "proposal_ids": [proposal.proposal_id for proposal in proposals],
            "metadata": payload.metadata,
        },
        timestamp=started_at,
        correlation_id=_correlation_id(context),
        actor_hash=_actor_hash(context),
        source=ANALYTICS_ENGINE_SOURCE,
    )
    record = state.repository.add_rl_kpi_iteration(
        RLKPIIterationRecord(
            iteration_id=iteration_id,
            tenant_id=context.tenant_id,
            periods=payload.periods,
            window_days=payload.window_days,
            policy_revision=payload.policy_revision,
            status=status,
            proposals=proposals,
            council_control=council_control,
            monitoring=monitoring,
            audit_hash=audit_record.audit_hash,
            correlation_id=_correlation_id(context),
            started_at=started_at,
            updated_at=started_at,
            metadata=payload.metadata,
        )
    )
    await state.publisher.publish(
        _rl_kpi_event_envelope(
            record=record,
            event_type=RL_KPI_ITERATION_CREATED_EVENT,
            occurred_at=started_at,
            audit_hash=audit_record.audit_hash,
        )
    )
    return _rl_kpi_iteration_response(record)


@router.get(
    "/analytics/rl-kpi/iterations/{iteration_id}",
    response_model=RLKPIIterationResponse,
    summary="Получить supervised RL-KPI итерацию tenant",
)
def get_rl_kpi_iteration(
    iteration_id: str,
    state: Annotated[AnalyticsEngineAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> RLKPIIterationResponse:
    require_access(RL_KPI_READ_POLICY, context=context)
    return _rl_kpi_iteration_response(
        state.repository.get_rl_kpi_iteration(
            context=context,
            iteration_id=iteration_id,
        )
    )


@router.post(
    "/analytics/rl-kpi/iterations/{iteration_id}/approval",
    response_model=RLKPIIterationResponse,
    summary="Зафиксировать решение Совета по RL-KPI предложениям",
)
async def approve_rl_kpi_iteration(
    iteration_id: str,
    payload: RLKPICouncilApprovalRequest,
    state: Annotated[AnalyticsEngineAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> RLKPIIterationResponse:
    actor_context = require_access(RL_KPI_APPROVAL_POLICY, context=context)
    decided_by = _required_subject(actor_context)
    decided_at = payload.decided_at or datetime.now(UTC)
    record = state.repository.get_rl_kpi_iteration(
        context=context,
        iteration_id=iteration_id,
    )
    if record.status is not RLKPIIterationStatus.AWAITING_COUNCIL_APPROVAL:
        raise SharedError(
            status_code=409,
            error_code="rl_kpi_iteration_not_awaiting_council_approval",
            message="Решение Совета доступно только для RL-KPI с предложениями",
            correlation_id=context.correlation_id,
        )

    proposal_status = (
        RLKPIProposalStatus.APPROVED
        if payload.decision is RLKPICouncilDecision.APPROVE
        else RLKPIProposalStatus.REJECTED
    )
    status = (
        RLKPIIterationStatus.APPROVED
        if payload.decision is RLKPICouncilDecision.APPROVE
        else RLKPIIterationStatus.REJECTED
    )
    proposals = tuple(
        proposal.model_copy(update={"status": proposal_status})
        for proposal in record.proposals
    )
    council_control = RLKPICouncilControl(
        required_role=COUNCIL_ROLE,
        approval_status=proposal_status.value,
        approved_by=decided_by,
        approved_at=decided_at,
        decision_ref=payload.decision_ref,
    )
    audit_record = state.audit_logger.record(
        event_type=RL_KPI_COUNCIL_DECISION_RECORDED_EVENT,
        tenant_id=context.tenant_id,
        metadata={
            "iteration_id": record.iteration_id,
            "decision": payload.decision.value,
            "decision_ref": payload.decision_ref,
            "comment_present": payload.comment is not None,
            "proposal_ids": [proposal.proposal_id for proposal in proposals],
        },
        timestamp=decided_at,
        correlation_id=_correlation_id(context),
        actor_hash=_actor_hash(context),
        source=ANALYTICS_ENGINE_SOURCE,
    )
    updated = state.repository.save_rl_kpi_iteration(
        RLKPIIterationRecord(
            iteration_id=record.iteration_id,
            tenant_id=record.tenant_id,
            periods=record.periods,
            window_days=record.window_days,
            policy_revision=record.policy_revision,
            status=status,
            proposals=proposals,
            council_control=council_control,
            monitoring=record.monitoring,
            audit_hash=audit_record.audit_hash,
            correlation_id=record.correlation_id,
            started_at=record.started_at,
            updated_at=decided_at,
            metadata=record.metadata,
        )
    )
    await state.publisher.publish(
        _rl_kpi_event_envelope(
            record=updated,
            event_type=RL_KPI_COUNCIL_DECISION_RECORDED_EVENT,
            occurred_at=decided_at,
            audit_hash=audit_record.audit_hash,
            extra_payload={
                "decision": payload.decision.value,
                "decision_ref": payload.decision_ref,
            },
        )
    )
    return _rl_kpi_iteration_response(updated)


@router.post(
    "/analytics/rl-kpi/iterations/{iteration_id}/effect",
    response_model=RLKPIIterationResponse,
    summary="Измерить эффект утверждённых RL-KPI изменений",
)
async def measure_rl_kpi_effect(
    iteration_id: str,
    payload: RLKPIEffectMeasurementRequest,
    state: Annotated[AnalyticsEngineAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> RLKPIIterationResponse:
    require_access(RL_KPI_EFFECT_POLICY, context=context)
    record = state.repository.get_rl_kpi_iteration(
        context=context,
        iteration_id=iteration_id,
    )
    if record.council_control.approval_status != RLKPIProposalStatus.APPROVED.value:
        raise SharedError(
            status_code=409,
            error_code="rl_kpi_requires_council_approval",
            message="Измерение эффекта доступно только после approval Совета",
            correlation_id=context.correlation_id,
        )

    measured_at = payload.measured_at or datetime.now(UTC)
    evaluation_kpi = build_analytics_kpi_response(
        tenant_id=context.tenant_id,
        period=payload.evaluation_period,
        events=state.repository.list_events(
            context=context,
            period=payload.evaluation_period,
        ),
    )
    proposals = _measure_rl_kpi_proposal_effects(
        proposals=record.proposals,
        evaluation_period=payload.evaluation_period,
        measured_at=measured_at,
        evaluation_kpi=evaluation_kpi,
    )
    effect_status = _overall_rl_kpi_effect_status(proposals)
    status = (
        RLKPIIterationStatus.ROLLBACK_REQUIRED
        if effect_status is RLKPIEffectStatus.DEGRADED
        else RLKPIIterationStatus.EFFECT_MEASURED
    )
    monitoring = RLKPIMonitoringSummary(
        baseline_period=record.monitoring.baseline_period,
        evaluation_period=payload.evaluation_period,
        effect_status=effect_status,
        metrics=record.monitoring.metrics,
    )
    audit_record = state.audit_logger.record(
        event_type=RL_KPI_EFFECT_MEASURED_EVENT,
        tenant_id=context.tenant_id,
        metadata={
            "iteration_id": record.iteration_id,
            "evaluation_period": payload.evaluation_period,
            "effect_status": effect_status.value,
            "rollback_required": status is RLKPIIterationStatus.ROLLBACK_REQUIRED,
            "metadata": payload.metadata,
        },
        timestamp=measured_at,
        correlation_id=_correlation_id(context),
        actor_hash=_actor_hash(context),
        source=ANALYTICS_ENGINE_SOURCE,
    )
    updated = state.repository.save_rl_kpi_iteration(
        RLKPIIterationRecord(
            iteration_id=record.iteration_id,
            tenant_id=record.tenant_id,
            periods=record.periods,
            window_days=record.window_days,
            policy_revision=record.policy_revision,
            status=status,
            proposals=proposals,
            council_control=record.council_control,
            monitoring=monitoring,
            audit_hash=audit_record.audit_hash,
            correlation_id=record.correlation_id,
            started_at=record.started_at,
            updated_at=measured_at,
            metadata=record.metadata,
        )
    )
    await state.publisher.publish(
        _rl_kpi_event_envelope(
            record=updated,
            event_type=RL_KPI_EFFECT_MEASURED_EVENT,
            occurred_at=measured_at,
            audit_hash=audit_record.audit_hash,
            extra_payload={
                "evaluation_period": payload.evaluation_period,
                "effect_status": effect_status.value,
            },
        )
    )
    return _rl_kpi_iteration_response(updated)


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


def subject_ref_hash(*, tenant_id: str, subject_id: str) -> str:
    return "sha256:" + hashlib.sha256(f"{tenant_id}:{subject_id}".encode()).hexdigest()


def _validate_period(value: str) -> str:
    if _PERIOD_RE.fullmatch(value) is None:
        raise ValueError("period должен соответствовать YYYY-MM или YYYY-Www")
    return value


def _build_rl_kpi_proposals(
    *,
    iteration_id: str,
    period_kpis: tuple[AnalyticsKPIResponse, ...],
    window_days: int,
) -> tuple[RLKPIOptimizationProposal, ...]:
    if not period_kpis:
        return ()

    latest_kpi = period_kpis[-1]
    proposals: list[RLKPIOptimizationProposal] = []
    for metric in latest_kpi.metrics:
        if metric.status is KPIStatus.ON_TRACK:
            continue

        values = tuple(
            period_metric.value
            for period_metric in _metric_series(period_kpis, metric.key)
        )
        baseline_value = _round_metric(sum(values) / len(values)) if values else 0
        direction = _expected_direction(metric)
        proposals.append(
            RLKPIOptimizationProposal(
                proposal_id=f"{iteration_id}:{metric.key}",
                metric_key=metric.key,
                category=metric.category,
                status=RLKPIProposalStatus.PROPOSED,
                policy_key=f"rl_kpi.{metric.key}",
                action=_rl_kpi_action(metric.key, direction),
                rationale_code=f"{metric.key}_{metric.status.value}",
                baseline_value=baseline_value,
                latest_value=metric.value,
                target_min=metric.target_min,
                target_max=metric.target_max,
                expected_direction=direction,
                expected_lift=_expected_lift(metric, direction),
                xai_summary=_rl_kpi_xai_summary(
                    metric=metric,
                    periods=tuple(kpi.period for kpi in period_kpis),
                    baseline_value=baseline_value,
                    window_days=window_days,
                ),
            )
        )

    return tuple(proposals)


def _metric_series(
    period_kpis: tuple[AnalyticsKPIResponse, ...],
    metric_key: str,
) -> tuple[KPIMetric, ...]:
    return tuple(
        metric
        for kpi in period_kpis
        for metric in kpi.metrics
        if metric.key == metric_key
    )


def _expected_direction(metric: KPIMetric) -> RLKPIExpectedDirection:
    if metric.status is KPIStatus.ABOVE_TARGET:
        return RLKPIExpectedDirection.DECREASE
    return RLKPIExpectedDirection.INCREASE


def _expected_lift(
    metric: KPIMetric,
    direction: RLKPIExpectedDirection,
) -> float:
    if direction is RLKPIExpectedDirection.INCREASE and metric.target_min is not None:
        return _round_metric(max(metric.target_min - metric.value, 0))
    if direction is RLKPIExpectedDirection.DECREASE and metric.target_max is not None:
        return _round_metric(max(metric.value - metric.target_max, 0))

    return 0.0


def _rl_kpi_action(metric_key: str, direction: RLKPIExpectedDirection) -> str:
    if direction is RLKPIExpectedDirection.DECREASE:
        return "stabilize_metric_with_council_review"

    return {
        "active_members": "increase_member_activation",
        "new_members": "review_onboarding_funnel",
        "materials_published": "rebalance_content_plan",
        "content_views": "increase_distribution_and_link_rotation",
        "avg_reading_minutes": "improve_article_structure",
        "comments": "run_engagement_prompt_experiment",
        "tasks_completed": "review_task_queue_and_notifications",
        "initiatives_created": "prepare_initiative_call",
    }.get(metric_key, "optimize_metric_under_council_control")


def _rl_kpi_xai_summary(
    *,
    metric: KPIMetric,
    periods: tuple[str, ...],
    baseline_value: float,
    window_days: int,
) -> str:
    target = _metric_target_label(metric)
    return (
        f"{metric.label}: последнее значение {_format_float(metric.value)} "
        f"{metric.unit}, окно {window_days} дней ({', '.join(periods)}), "
        f"среднее окна {_format_float(baseline_value)}, {target}; "
        "предложение требует approval Совета."
    )


def _measure_rl_kpi_proposal_effects(
    *,
    proposals: tuple[RLKPIOptimizationProposal, ...],
    evaluation_period: str,
    measured_at: datetime,
    evaluation_kpi: AnalyticsKPIResponse,
) -> tuple[RLKPIOptimizationProposal, ...]:
    evaluation_metrics = {metric.key: metric for metric in evaluation_kpi.metrics}
    return tuple(
        proposal.model_copy(
            update={
                "effect": _measure_rl_kpi_effect(
                    proposal=proposal,
                    evaluation_metric=evaluation_metrics[proposal.metric_key],
                    evaluation_period=evaluation_period,
                    measured_at=measured_at,
                )
            }
        )
        for proposal in proposals
        if proposal.metric_key in evaluation_metrics
    )


def _measure_rl_kpi_effect(
    *,
    proposal: RLKPIOptimizationProposal,
    evaluation_metric: KPIMetric,
    evaluation_period: str,
    measured_at: datetime,
) -> RLKPIEffectMeasurement:
    baseline_value = proposal.latest_value
    evaluation_value = evaluation_metric.value
    absolute_delta = _round_metric(evaluation_value - baseline_value)
    relative_delta = None
    if baseline_value > 0:
        relative_delta = _round_metric(absolute_delta / baseline_value)

    status = _proposal_effect_status(
        direction=proposal.expected_direction,
        absolute_delta=absolute_delta,
    )
    return RLKPIEffectMeasurement(
        evaluation_period=evaluation_period,
        measured_at=measured_at,
        baseline_value=baseline_value,
        evaluation_value=evaluation_value,
        absolute_delta=absolute_delta,
        relative_delta=relative_delta,
        status=status,
        rollback_recommended=status is RLKPIEffectStatus.DEGRADED,
    )


def _proposal_effect_status(
    *,
    direction: RLKPIExpectedDirection,
    absolute_delta: float,
) -> RLKPIEffectStatus:
    if absolute_delta == 0:
        return RLKPIEffectStatus.STABLE
    if direction is RLKPIExpectedDirection.INCREASE:
        return (
            RLKPIEffectStatus.IMPROVED
            if absolute_delta > 0
            else RLKPIEffectStatus.DEGRADED
        )

    return (
        RLKPIEffectStatus.IMPROVED if absolute_delta < 0 else RLKPIEffectStatus.DEGRADED
    )


def _overall_rl_kpi_effect_status(
    proposals: tuple[RLKPIOptimizationProposal, ...],
) -> RLKPIEffectStatus:
    statuses = tuple(
        proposal.effect.status for proposal in proposals if proposal.effect is not None
    )
    if not statuses:
        return RLKPIEffectStatus.STABLE
    if RLKPIEffectStatus.DEGRADED in statuses:
        return RLKPIEffectStatus.DEGRADED
    if RLKPIEffectStatus.IMPROVED in statuses:
        return RLKPIEffectStatus.IMPROVED

    return RLKPIEffectStatus.STABLE


def _rl_kpi_event_envelope(
    *,
    record: RLKPIIterationRecord,
    event_type: str,
    occurred_at: datetime,
    audit_hash: str,
    extra_payload: dict[str, JSONValue] | None = None,
) -> EventEnvelope:
    payload: dict[str, JSONValue] = {
        "iteration_id": record.iteration_id,
        "periods": list(record.periods),
        "window_days": record.window_days,
        "policy_revision": record.policy_revision,
        "status": record.status.value,
        "proposal_count": len(record.proposals),
        "audit_hash": audit_hash,
    }
    if extra_payload is not None:
        payload.update(extra_payload)

    return EventEnvelope(
        event_id=_new_id("evt-rl-kpi"),
        type=event_type,
        schema_version=ANALYTICS_ENGINE_SCHEMA_VERSION,
        tenant_id=record.tenant_id,
        source=ANALYTICS_ENGINE_SOURCE,
        correlation_id=record.correlation_id,
        occurred_at=occurred_at,
        payload=payload,
    )


def _rl_kpi_iteration_response(
    record: RLKPIIterationRecord,
) -> RLKPIIterationResponse:
    return RLKPIIterationResponse(
        iteration_id=record.iteration_id,
        tenant_id=record.tenant_id,
        periods=record.periods,
        window_days=record.window_days,
        policy_revision=record.policy_revision,
        status=record.status,
        proposal_count=len(record.proposals),
        proposals=record.proposals,
        council_control=record.council_control,
        monitoring=record.monitoring,
        audit_hash=record.audit_hash,
        started_at=record.started_at,
        updated_at=record.updated_at,
    )


def _required_subject(context: TenantContext) -> SubjectId:
    if context.subject is None or context.subject.strip() == "":
        raise SharedError(
            status_code=400,
            error_code="actor_required",
            message="RL-KPI approval требует subject в tenant context",
            correlation_id=context.correlation_id,
        )

    return context.subject


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


def _add_analytics_event(
    *,
    state: AnalyticsEngineAPIState,
    context: TenantContext,
    event_id: str,
    event_type: AnalyticsEventType,
    period: str,
    value: float,
    sample_count: int | None,
    member_hash: str | None,
    occurred_at: datetime,
    metadata: dict[str, JSONValue],
) -> AnalyticsEventRecord:
    if state.repository.event_exists(tenant_id=context.tenant_id, event_id=event_id):
        raise SharedError(
            status_code=409,
            error_code="analytics_event_conflict",
            message="Аналитическое событие с таким event_id уже существует",
            correlation_id=context.correlation_id,
        )

    category = _EVENT_CATEGORIES[event_type]
    correlation_id = _correlation_id(context)
    audit_record = state.audit_logger.record(
        event_type=ANALYTICS_EVENT_RECORDED_EVENT,
        tenant_id=context.tenant_id,
        metadata={
            "event_id": event_id,
            "type": event_type.value,
            "category": category.value,
            "period": period,
            "value": value,
            "sample_count": sample_count,
            "member_hash": member_hash,
            "metadata": metadata,
        },
        timestamp=occurred_at,
        correlation_id=correlation_id,
        actor_hash=_actor_hash(context),
        source=ANALYTICS_ENGINE_SOURCE,
    )
    return state.repository.add_event(
        AnalyticsEventRecord(
            event_id=event_id,
            tenant_id=context.tenant_id,
            type=event_type.value,
            category=category.value,
            period=period,
            value=value,
            sample_count=sample_count,
            member_hash=member_hash,
            audit_hash=audit_record.audit_hash,
            correlation_id=correlation_id,
            occurred_at=occurred_at,
            metadata=metadata,
        )
    )


def _add_pilot_usage_signal(
    *,
    state: AnalyticsEngineAPIState,
    context: TenantContext,
    batch_id: str,
    source: str,
    period: str,
    payload: PilotUsageTelemetryCreate,
    index: int,
    collected_at: datetime,
) -> PilotUsageTelemetryRecord:
    signal_id = payload.signal_id or f"{batch_id}:usage:{index}"
    occurred_at = payload.occurred_at or collected_at
    correlation_id = _correlation_id(context)
    audit_record = state.audit_logger.record(
        event_type=PILOT_USAGE_RECORDED_EVENT,
        tenant_id=context.tenant_id,
        metadata={
            "batch_id": batch_id,
            "signal_id": signal_id,
            "source": source,
            "period": period,
            "name": payload.name,
            "value": payload.value,
            "unit": payload.unit,
            "metadata": payload.metadata,
        },
        timestamp=occurred_at,
        correlation_id=correlation_id,
        actor_hash=_actor_hash(context),
        source=source,
    )
    return state.repository.add_usage_signal(
        PilotUsageTelemetryRecord(
            signal_id=signal_id,
            tenant_id=context.tenant_id,
            source=source,
            period=period,
            name=payload.name,
            value=payload.value,
            unit=payload.unit,
            audit_hash=audit_record.audit_hash,
            correlation_id=correlation_id,
            occurred_at=occurred_at,
            metadata=payload.metadata,
        )
    )


def _add_pilot_incident(
    *,
    state: AnalyticsEngineAPIState,
    context: TenantContext,
    batch_id: str,
    source: str,
    period: str,
    payload: PilotIncidentCreate,
    index: int,
    collected_at: datetime,
) -> PilotIncidentRecord:
    incident_id = payload.incident_id or f"{batch_id}:incident:{index}"
    occurred_at = payload.occurred_at or collected_at
    correlation_id = _correlation_id(context)
    audit_record = state.audit_logger.record(
        event_type=PILOT_INCIDENT_RECORDED_EVENT,
        tenant_id=context.tenant_id,
        metadata={
            "batch_id": batch_id,
            "incident_id": incident_id,
            "source": source,
            "period": period,
            "severity": payload.severity.value,
            "status": payload.status.value,
            "title": payload.title,
            "impact": payload.impact,
            "metadata": payload.metadata,
        },
        timestamp=occurred_at,
        correlation_id=correlation_id,
        actor_hash=_actor_hash(context),
        source=source,
    )
    return state.repository.add_incident(
        PilotIncidentRecord(
            incident_id=incident_id,
            tenant_id=context.tenant_id,
            source=source,
            period=period,
            severity=payload.severity.value,
            status=payload.status.value,
            title=payload.title,
            impact=payload.impact,
            audit_hash=audit_record.audit_hash,
            correlation_id=correlation_id,
            occurred_at=occurred_at,
            resolved_at=payload.resolved_at,
            metadata=payload.metadata,
        )
    )


def _pilot_kpi_event_specs(
    payload: PilotTelemetryKPIRequest,
) -> tuple[tuple[AnalyticsEventType, float, int | None], ...]:
    specs: list[tuple[AnalyticsEventType, float, int | None]] = []
    _append_kpi_spec(specs, AnalyticsEventType.MEMBER_ACTIVE, payload.active_members)
    _append_kpi_spec(specs, AnalyticsEventType.MEMBER_JOINED, payload.new_members)
    _append_kpi_spec(
        specs,
        AnalyticsEventType.MATERIAL_PUBLISHED,
        payload.materials_published,
    )
    _append_kpi_spec(specs, AnalyticsEventType.CONTENT_VIEWED, payload.content_views)
    _append_kpi_spec(
        specs,
        AnalyticsEventType.READING_TIME_RECORDED,
        payload.reading_seconds,
        payload.reading_sessions,
    )
    _append_kpi_spec(specs, AnalyticsEventType.COMMENT_CREATED, payload.comments)
    _append_kpi_spec(
        specs,
        AnalyticsEventType.TASK_COMPLETED,
        payload.tasks_completed,
    )
    _append_kpi_spec(
        specs,
        AnalyticsEventType.INITIATIVE_CREATED,
        payload.initiatives_created,
    )
    return tuple(specs)


def _append_kpi_spec(
    specs: list[tuple[AnalyticsEventType, float, int | None]],
    event_type: AnalyticsEventType,
    value: float | None,
    sample_count: int | None = None,
) -> None:
    if value is None:
        return

    specs.append((event_type, value, sample_count))


def _build_pilot_usage_summary(
    records: Iterable[PilotUsageTelemetryRecord],
) -> PilotUsageTelemetrySummary:
    record_list = tuple(records)
    grouped: dict[tuple[str, str], list[PilotUsageTelemetryRecord]] = {}
    for record in record_list:
        grouped.setdefault((record.name, record.unit), []).append(record)

    metrics = tuple(
        PilotUsageMetricSummary(
            name=name,
            unit=unit,
            value=_round_metric(sum(record.value for record in records_for_metric)),
            signal_count=len(records_for_metric),
            sources=tuple(
                sorted({record.source for record in records_for_metric}),
            ),
        )
        for (name, unit), records_for_metric in sorted(grouped.items())
    )
    return PilotUsageTelemetrySummary(
        signals_total=len(record_list),
        metrics=metrics,
    )


def _build_pilot_incident_summary(
    records: Iterable[PilotIncidentRecord],
) -> PilotIncidentSummary:
    record_list = tuple(records)
    severity_counts: dict[str, int] = {}
    for record in record_list:
        severity_counts[record.severity] = severity_counts.get(record.severity, 0) + 1

    return PilotIncidentSummary(
        incidents_total=len(record_list),
        open_incidents=sum(
            1
            for record in record_list
            if record.status == PilotIncidentStatus.OPEN.value
        ),
        resolved_incidents=sum(
            1
            for record in record_list
            if record.status == PilotIncidentStatus.RESOLVED.value
        ),
        severity_counts={
            severity: severity_counts[severity] for severity in sorted(severity_counts)
        },
        latest=tuple(_pilot_incident_item(record) for record in record_list[:5]),
    )


def _pilot_incident_item(record: PilotIncidentRecord) -> PilotIncidentReportItem:
    return PilotIncidentReportItem(
        incident_id=record.incident_id,
        severity=PilotIncidentSeverity(record.severity),
        status=PilotIncidentStatus(record.status),
        title=record.title,
        impact=record.impact,
        occurred_at=record.occurred_at,
        resolved_at=record.resolved_at,
    )


def _build_pilot_feedback_loop(
    *,
    kpi: AnalyticsKPIResponse,
    incidents: PilotIncidentSummary,
) -> PilotFeedbackLoopSummary:
    signals: list[str] = []
    if kpi.summary.metrics_below_target > 0:
        signals.append("kpi:below_target")
    else:
        signals.append("kpi:on_track")

    if incidents.open_incidents > 0:
        signals.append("incidents:open")
    elif incidents.incidents_total > 0:
        signals.append("incidents:resolved")
    else:
        signals.append("incidents:none")

    status = "ready_for_council_review"
    if kpi.summary.metrics_below_target > 0 or incidents.open_incidents > 0:
        status = "needs_council_attention"

    return PilotFeedbackLoopSummary(status=status, signals=tuple(signals))


def _report_frequency(period: str) -> str:
    if "-W" in period:
        return "weekly"

    return "monthly"


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


def _metric_target_label(metric: KPIMetric) -> str:
    if metric.target_min is None and metric.target_max is None:
        return metric.target_window
    if metric.target_min is None:
        return f"цель до {_format_float(metric.target_max or 0)}"
    if metric.target_max is None:
        return f"цель от {_format_float(metric.target_min)}"
    return f"цель {_format_float(metric.target_min)}-{_format_float(metric.target_max)}"


def _format_float(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


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
