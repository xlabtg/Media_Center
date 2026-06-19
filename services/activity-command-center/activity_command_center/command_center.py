from __future__ import annotations

import hashlib
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
    SharedBaseModel,
    SubjectId,
    TenantId,
)

ACTIVITY_COMMAND_CENTER_SOURCE = "activity-command-center"
ACTIVITY_COMMAND_CENTER_SCHEMA_VERSION = "1.0"
THRESHOLDS_UPDATED_EVENT = "activity.thresholds.updated"
TASK_CREATED_EVENT = "activity.task.created"

DEFAULT_MAX_AUTONOMOUS_RISK_SCORE = 0.7
DEFAULT_MIN_AGENT_CONFIDENCE = 0.65
DEFAULT_OPERATIONAL_QUEUE_LIMIT = 50
DEFAULT_STRATEGIC_QUEUE_LIMIT = 25
DEFAULT_ADAPTIVE_QUEUE_LIMIT = 10
DEFAULT_THRESHOLD_AUDIT_HASH = "0" * 64

_TASK_TYPE_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"


class ActivityCommandCenterError(RuntimeError):
    """Base error for Activity Command Center domain rule violations."""


class ActivityTaskAlreadyExistsError(ActivityCommandCenterError):
    """Raised when a tenant task id already exists."""


class FeedbackLoop(StrEnum):
    OPERATIONAL = "operational"
    STRATEGIC = "strategic"
    ADAPTIVE = "adaptive"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    NEEDS_COUNCIL_REVIEW = "needs_council_review"
    COMPLETED = "completed"
    CANCELED = "canceled"


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    ESCALATE = "escalate"


class FeedbackLoopConfig(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    loop: FeedbackLoop
    min_hours: int = Field(ge=1)
    max_hours: int = Field(ge=1)
    window_hours: int = Field(ge=1)


class ThresholdSettings(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    tenant_id: TenantId
    revision: int = Field(ge=1)
    max_autonomous_risk_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    min_agent_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    operational_queue_limit: int = Field(ge=1, le=10_000)
    strategic_queue_limit: int = Field(ge=1, le=10_000)
    adaptive_queue_limit: int = Field(ge=1, le=10_000)
    updated_by: SubjectId | None = None
    updated_at: datetime
    audit_hash: AuditHash = DEFAULT_THRESHOLD_AUDIT_HASH
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("updated_at")
    @classmethod
    def _normalize_updated_at(cls, value: datetime) -> datetime:
        return normalize_datetime(value)

    def queue_limit_for(self, feedback_loop: FeedbackLoop) -> int:
        if feedback_loop is FeedbackLoop.OPERATIONAL:
            return self.operational_queue_limit
        if feedback_loop is FeedbackLoop.STRATEGIC:
            return self.strategic_queue_limit

        return self.adaptive_queue_limit


class ActivityTask(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    task_id: IdempotencyKey
    tenant_id: TenantId
    task_type: str = Field(pattern=_TASK_TYPE_PATTERN)
    title: str = Field(min_length=1, max_length=256)
    payload: dict[str, JSONValue] = Field(default_factory=dict)
    status: TaskStatus
    assignee: SubjectId
    created_by: SubjectId
    agent_id: SubjectId | None = None
    risk_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    agent_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    feedback_loop: FeedbackLoop
    policy_decision: PolicyDecision
    policy_revision: int = Field(ge=1)
    policy_reasons: tuple[str, ...] = Field(default_factory=tuple)
    due_at: datetime
    audit_hash: AuditHash
    created_at: datetime
    updated_at: datetime

    @field_validator("due_at", "created_at", "updated_at")
    @classmethod
    def _normalize_datetime_field(cls, value: datetime) -> datetime:
        return normalize_datetime(value)

    @field_validator("policy_reasons")
    @classmethod
    def _validate_policy_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for reason in value:
            if not isinstance(reason, str) or reason.strip() == "":
                raise ValueError("policy_reasons должен содержать непустые строки")
        return value


class FeedbackLoopOverview(SharedBaseModel):
    queued: int = Field(ge=0)
    needs_council_review: int = Field(ge=0)
    completed: int = Field(ge=0)
    canceled: int = Field(ge=0)
    window_hours: int = Field(ge=1)


class ActivityOverview(SharedBaseModel):
    tenant_id: TenantId
    thresholds: ThresholdSettings
    queue_total: int = Field(ge=0)
    queue_by_status: dict[str, int] = Field(default_factory=dict)
    feedback_loops: dict[str, FeedbackLoopOverview] = Field(default_factory=dict)
    generated_at: datetime

    @field_validator("generated_at")
    @classmethod
    def _normalize_generated_at(cls, value: datetime) -> datetime:
        return normalize_datetime(value)


class TaskListResponse(SharedBaseModel):
    items: tuple[ActivityTask, ...]


@dataclass(frozen=True, slots=True)
class ThresholdUpdateInput:
    max_autonomous_risk_score: float | None = None
    min_agent_confidence: float | None = None
    operational_queue_limit: int | None = None
    strategic_queue_limit: int | None = None
    adaptive_queue_limit: int | None = None
    metadata: Mapping[str, JSONValue] | None = None


@dataclass(frozen=True, slots=True)
class TaskCreateInput:
    task_type: str
    title: str
    payload: Mapping[str, JSONValue]
    assignee: str
    risk_score: float
    agent_confidence: float
    feedback_loop: FeedbackLoop
    task_id: str | None = None
    event_id: str | None = None
    agent_id: str | None = None


@dataclass(slots=True)
class InMemoryActivityRepository:
    _thresholds: dict[str, ThresholdSettings] = field(default_factory=dict)
    _tasks: dict[tuple[str, str], ActivityTask] = field(default_factory=dict)

    def get_thresholds(self, *, tenant_id: str) -> ThresholdSettings:
        thresholds = self._thresholds.get(tenant_id)
        if thresholds is not None:
            return thresholds

        default_thresholds = ThresholdSettings(
            tenant_id=tenant_id,
            revision=1,
            max_autonomous_risk_score=DEFAULT_MAX_AUTONOMOUS_RISK_SCORE,
            min_agent_confidence=DEFAULT_MIN_AGENT_CONFIDENCE,
            operational_queue_limit=DEFAULT_OPERATIONAL_QUEUE_LIMIT,
            strategic_queue_limit=DEFAULT_STRATEGIC_QUEUE_LIMIT,
            adaptive_queue_limit=DEFAULT_ADAPTIVE_QUEUE_LIMIT,
            updated_at=datetime(1970, 1, 1, tzinfo=UTC),
            metadata={"source": "default"},
        )
        self._thresholds[tenant_id] = default_thresholds
        return default_thresholds

    def save_thresholds(self, thresholds: ThresholdSettings) -> ThresholdSettings:
        self._thresholds[thresholds.tenant_id] = thresholds
        return thresholds

    def add_task(self, task: ActivityTask) -> ActivityTask:
        key = _task_key(task.tenant_id, task.task_id)
        if key in self._tasks:
            raise ActivityTaskAlreadyExistsError(
                "Задача с таким task_id уже есть в очереди tenant"
            )

        self._tasks[key] = task
        return task

    def list_tasks(
        self,
        *,
        tenant_id: str,
        status: TaskStatus | None = None,
        feedback_loop: FeedbackLoop | None = None,
    ) -> tuple[ActivityTask, ...]:
        tasks = (
            task
            for (record_tenant_id, _task_id), task in self._tasks.items()
            if record_tenant_id == tenant_id
        )
        if status is not None:
            tasks = (task for task in tasks if task.status is status)
        if feedback_loop is not None:
            tasks = (task for task in tasks if task.feedback_loop is feedback_loop)

        return tuple(sorted(tasks, key=lambda task: task.created_at))

    def active_task_count(
        self,
        *,
        tenant_id: str,
        feedback_loop: FeedbackLoop,
    ) -> int:
        return sum(
            1
            for task in self.list_tasks(
                tenant_id=tenant_id,
                feedback_loop=feedback_loop,
            )
            if task.status in {TaskStatus.QUEUED, TaskStatus.NEEDS_COUNCIL_REVIEW}
        )


@dataclass(slots=True)
class ActivityCommandCenter:
    publisher: EventPublisher = field(default_factory=InMemoryEventBus)
    repository: InMemoryActivityRepository = field(
        default_factory=InMemoryActivityRepository
    )
    audit_logger: AuditLogger = field(default_factory=AuditLogger)

    async def update_thresholds(
        self,
        *,
        tenant_id: str,
        updated_by: str,
        correlation_id: str,
        update: ThresholdUpdateInput,
        updated_at: datetime | str | None = None,
        event_id: str | None = None,
    ) -> ThresholdSettings:
        existing = self.repository.get_thresholds(tenant_id=tenant_id)
        changed_at = normalize_datetime(updated_at or datetime.now(UTC))
        actor_hash = subject_ref_hash(tenant_id=tenant_id, subject_id=updated_by)
        metadata = dict(update.metadata or {})
        revision = existing.revision + 1
        max_autonomous_risk_score = _coalesce(
            update.max_autonomous_risk_score,
            existing.max_autonomous_risk_score,
        )
        min_agent_confidence = _coalesce(
            update.min_agent_confidence,
            existing.min_agent_confidence,
        )
        operational_queue_limit = _coalesce(
            update.operational_queue_limit,
            existing.operational_queue_limit,
        )
        strategic_queue_limit = _coalesce(
            update.strategic_queue_limit,
            existing.strategic_queue_limit,
        )
        adaptive_queue_limit = _coalesce(
            update.adaptive_queue_limit,
            existing.adaptive_queue_limit,
        )
        audit_record = self.audit_logger.record(
            event_type=THRESHOLDS_UPDATED_EVENT,
            tenant_id=tenant_id,
            metadata={
                "revision": revision,
                "max_autonomous_risk_score": max_autonomous_risk_score,
                "min_agent_confidence": min_agent_confidence,
                "operational_queue_limit": operational_queue_limit,
                "strategic_queue_limit": strategic_queue_limit,
                "adaptive_queue_limit": adaptive_queue_limit,
                "metadata": metadata,
            },
            timestamp=changed_at,
            correlation_id=correlation_id,
            actor_hash=actor_hash,
            source=ACTIVITY_COMMAND_CENTER_SOURCE,
        )
        thresholds = ThresholdSettings(
            tenant_id=tenant_id,
            revision=revision,
            max_autonomous_risk_score=max_autonomous_risk_score,
            min_agent_confidence=min_agent_confidence,
            operational_queue_limit=operational_queue_limit,
            strategic_queue_limit=strategic_queue_limit,
            adaptive_queue_limit=adaptive_queue_limit,
            updated_by=updated_by,
            updated_at=changed_at,
            audit_hash=audit_record.audit_hash,
            metadata=metadata,
        )
        self.repository.save_thresholds(thresholds)

        await self.publisher.publish(
            EventEnvelope(
                event_id=event_id or _new_id("evt-thresholds-updated"),
                type=THRESHOLDS_UPDATED_EVENT,
                schema_version=ACTIVITY_COMMAND_CENTER_SCHEMA_VERSION,
                tenant_id=tenant_id,
                source=ACTIVITY_COMMAND_CENTER_SOURCE,
                correlation_id=correlation_id,
                occurred_at=changed_at,
                payload={
                    "revision": thresholds.revision,
                    "audit_hash": thresholds.audit_hash,
                    "max_autonomous_risk_score": (thresholds.max_autonomous_risk_score),
                    "min_agent_confidence": thresholds.min_agent_confidence,
                },
            )
        )
        return thresholds

    async def create_task(
        self,
        *,
        tenant_id: str,
        created_by: str,
        correlation_id: str,
        task: TaskCreateInput,
        created_at: datetime | str | None = None,
    ) -> ActivityTask:
        queued_at = normalize_datetime(created_at or datetime.now(UTC))
        thresholds = self.repository.get_thresholds(tenant_id=tenant_id)
        reasons = self._policy_reasons(
            tenant_id=tenant_id,
            thresholds=thresholds,
            feedback_loop=task.feedback_loop,
            risk_score=task.risk_score,
            agent_confidence=task.agent_confidence,
        )
        policy_decision = PolicyDecision.ESCALATE if reasons else PolicyDecision.ALLOW
        status = (
            TaskStatus.NEEDS_COUNCIL_REVIEW
            if policy_decision is PolicyDecision.ESCALATE
            else TaskStatus.QUEUED
        )
        resolved_task_id = task.task_id or _new_id("task")
        assignee_hash = subject_ref_hash(
            tenant_id=tenant_id,
            subject_id=task.assignee,
        )
        created_by_hash = subject_ref_hash(
            tenant_id=tenant_id,
            subject_id=created_by,
        )
        agent_hash = (
            subject_ref_hash(tenant_id=tenant_id, subject_id=task.agent_id)
            if task.agent_id is not None
            else None
        )
        audit_record = self.audit_logger.record(
            event_type=TASK_CREATED_EVENT,
            tenant_id=tenant_id,
            metadata={
                "task_id": resolved_task_id,
                "task_type": task.task_type,
                "status": status.value,
                "feedback_loop": task.feedback_loop.value,
                "policy_decision": policy_decision.value,
                "policy_revision": thresholds.revision,
                "policy_reasons": list(reasons),
                "risk_score": task.risk_score,
                "agent_confidence": task.agent_confidence,
                "assignee_hash": assignee_hash,
                "agent_hash": agent_hash,
                "created_by_hash": created_by_hash,
            },
            timestamp=queued_at,
            correlation_id=correlation_id,
            actor_hash=created_by_hash,
            source=ACTIVITY_COMMAND_CENTER_SOURCE,
        )
        due_at = queued_at + timedelta(
            hours=loop_config(task.feedback_loop).window_hours
        )
        created_task = ActivityTask(
            task_id=resolved_task_id,
            tenant_id=tenant_id,
            task_type=task.task_type,
            title=task.title,
            payload=dict(task.payload),
            status=status,
            assignee=task.assignee,
            created_by=created_by,
            agent_id=task.agent_id,
            risk_score=task.risk_score,
            agent_confidence=task.agent_confidence,
            feedback_loop=task.feedback_loop,
            policy_decision=policy_decision,
            policy_revision=thresholds.revision,
            policy_reasons=reasons,
            due_at=due_at,
            audit_hash=audit_record.audit_hash,
            created_at=queued_at,
            updated_at=queued_at,
        )
        self.repository.add_task(created_task)

        await self.publisher.publish(
            EventEnvelope(
                event_id=task.event_id or _new_id("evt-task-created"),
                type=TASK_CREATED_EVENT,
                schema_version=ACTIVITY_COMMAND_CENTER_SCHEMA_VERSION,
                tenant_id=tenant_id,
                source=ACTIVITY_COMMAND_CENTER_SOURCE,
                correlation_id=correlation_id,
                occurred_at=queued_at,
                payload={
                    "task_id": created_task.task_id,
                    "task_type": created_task.task_type,
                    "status": created_task.status.value,
                    "feedback_loop": created_task.feedback_loop.value,
                    "policy_decision": created_task.policy_decision.value,
                    "policy_revision": created_task.policy_revision,
                    "due_at": format_datetime(created_task.due_at),
                    "audit_hash": created_task.audit_hash,
                },
            )
        )
        return created_task

    def list_tasks(
        self,
        *,
        tenant_id: str,
        status: TaskStatus | None = None,
        feedback_loop: FeedbackLoop | None = None,
    ) -> tuple[ActivityTask, ...]:
        return self.repository.list_tasks(
            tenant_id=tenant_id,
            status=status,
            feedback_loop=feedback_loop,
        )

    def get_thresholds(self, *, tenant_id: str) -> ThresholdSettings:
        return self.repository.get_thresholds(tenant_id=tenant_id)

    def overview(
        self,
        *,
        tenant_id: str,
        generated_at: datetime | str | None = None,
    ) -> ActivityOverview:
        tasks = self.repository.list_tasks(tenant_id=tenant_id)
        queue_by_status = _queue_by_status(tasks)
        feedback_loops = {
            feedback_loop.value: _feedback_loop_overview(tasks, feedback_loop)
            for feedback_loop in FeedbackLoop
        }
        return ActivityOverview(
            tenant_id=tenant_id,
            thresholds=self.repository.get_thresholds(tenant_id=tenant_id),
            queue_total=len(tasks),
            queue_by_status=queue_by_status,
            feedback_loops=feedback_loops,
            generated_at=normalize_datetime(generated_at or datetime.now(UTC)),
        )

    def _policy_reasons(
        self,
        *,
        tenant_id: str,
        thresholds: ThresholdSettings,
        feedback_loop: FeedbackLoop,
        risk_score: float,
        agent_confidence: float,
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if risk_score > thresholds.max_autonomous_risk_score:
            reasons.append("risk_score_above_threshold")
        if agent_confidence < thresholds.min_agent_confidence:
            reasons.append("confidence_below_threshold")
        active_count = self.repository.active_task_count(
            tenant_id=tenant_id,
            feedback_loop=feedback_loop,
        )
        if active_count >= thresholds.queue_limit_for(feedback_loop):
            reasons.append("queue_limit_reached")

        return tuple(reasons)


def loop_config(feedback_loop: FeedbackLoop) -> FeedbackLoopConfig:
    return FEEDBACK_LOOP_CONFIGS[feedback_loop]


def subject_ref_hash(*, tenant_id: str, subject_id: str) -> str:
    return "sha256:" + hashlib.sha256(f"{tenant_id}:{subject_id}".encode()).hexdigest()


def normalize_datetime(value: datetime | str) -> datetime:
    if isinstance(value, str):
        normalized = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        normalized = value

    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=UTC)

    return normalized.astimezone(UTC)


def format_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _queue_by_status(tasks: tuple[ActivityTask, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for task in tasks:
        counts[task.status.value] = counts.get(task.status.value, 0) + 1
    return counts


def _feedback_loop_overview(
    tasks: tuple[ActivityTask, ...],
    feedback_loop: FeedbackLoop,
) -> FeedbackLoopOverview:
    statuses = {status.value: 0 for status in TaskStatus}
    for task in tasks:
        if task.feedback_loop is feedback_loop:
            statuses[task.status.value] += 1

    return FeedbackLoopOverview(
        queued=statuses[TaskStatus.QUEUED.value],
        needs_council_review=statuses[TaskStatus.NEEDS_COUNCIL_REVIEW.value],
        completed=statuses[TaskStatus.COMPLETED.value],
        canceled=statuses[TaskStatus.CANCELED.value],
        window_hours=loop_config(feedback_loop).window_hours,
    )


def _coalesce[T](value: T | None, fallback: T) -> T:
    if value is None:
        return fallback

    return value


def _task_key(tenant_id: str, task_id: str) -> tuple[str, str]:
    return tenant_id, task_id


def _new_id(prefix: str) -> IdempotencyKey:
    return f"{prefix}-{uuid4()}"


FEEDBACK_LOOP_CONFIGS: dict[FeedbackLoop, FeedbackLoopConfig] = {
    FeedbackLoop.OPERATIONAL: FeedbackLoopConfig(
        loop=FeedbackLoop.OPERATIONAL,
        min_hours=1,
        max_hours=24,
        window_hours=8,
    ),
    FeedbackLoop.STRATEGIC: FeedbackLoopConfig(
        loop=FeedbackLoop.STRATEGIC,
        min_hours=24,
        max_hours=72,
        window_hours=48,
    ),
    FeedbackLoop.ADAPTIVE: FeedbackLoopConfig(
        loop=FeedbackLoop.ADAPTIVE,
        min_hours=168,
        max_hours=720,
        window_hours=336,
    ),
}
