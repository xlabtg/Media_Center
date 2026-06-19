from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
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

NEURO_AGENT_SOURCE = "neuro-agent-orchestrator"
NEURO_AGENT_SCHEMA_VERSION = "1.0"
THRESHOLDS_UPDATED_EVENT = "neuro_agent.thresholds.updated"
AUDIENCE_PROFILE_CREATED_EVENT = "neuro_agent.audience_profile.created"
AUTO_REPLY_SENT_EVENT = "neuro_agent.auto_reply.sent"
AUTO_REPLY_ESCALATED_EVENT = "neuro_agent.auto_reply.escalated"

DEFAULT_MAX_AUTONOMOUS_RISK_SCORE = 0.45
DEFAULT_MIN_AGENT_CONFIDENCE = 0.75
DEFAULT_MAX_AUTONOMOUS_RECIPIENTS = 5
DEFAULT_ALLOWED_TEMPLATE_KEYS = ("welcome", "faq_basic", "participation_rules")
DEFAULT_THRESHOLD_AUDIT_HASH = "0" * 64

_TEMPLATE_KEY_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
_PLATFORM_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"
_BLOCKED_META_PLATFORMS = frozenset({"facebook", "instagram"})


class NeuroAgentOrchestratorError(RuntimeError):
    """Base error for Neuro-Agent Orchestrator domain rule violations."""


class AgentRunAlreadyExistsError(NeuroAgentOrchestratorError):
    """Raised when a tenant run id is reused."""


class PdnScopeViolationError(NeuroAgentOrchestratorError):
    """Raised when an audience source violates open-data/PDn constraints."""

    def __init__(self, reasons: tuple[str, ...]) -> None:
        self.reasons = reasons
        super().__init__("Источник аудитории нарушает ограничения ПДн/open-data")


class AgentTaskType(StrEnum):
    AUDIENCE_ANALYSIS = "audience_analysis"
    ENGAGEMENT_AUTO_REPLY = "engagement_auto_reply"


class AgentRunStatus(StrEnum):
    COMPLETED = "completed"
    NEEDS_COUNCIL_REVIEW = "needs_council_review"


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    ESCALATE = "escalate"


class AccessScope(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    INTERNAL = "internal"
    PARTNER = "partner"


class ToSStatus(StrEnum):
    ALLOWED = "allowed"
    RESTRICTED = "restricted"
    BLOCKED = "blocked"
    LEGAL_REVIEW = "legal_review"


class LegalBasis(StrEnum):
    PUBLIC_INTEREST = "public_interest"
    CONSENT = "consent"
    CONTRACT = "contract"
    MISSING = "missing"


class AutoReplyStatus(StrEnum):
    SENT = "sent"
    NEEDS_COUNCIL_REVIEW = "needs_council_review"


class AudienceMetrics(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    followers: int = Field(default=0, ge=0)
    comments: int = Field(default=0, ge=0)
    reactions: int = Field(default=0, ge=0)
    shares: int = Field(default=0, ge=0)

    def reach(self) -> int:
        return self.followers

    def engagement_count(self) -> int:
        return self.comments + self.reactions + self.shares


class AudienceSource(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    source_id: IdempotencyKey
    platform: str = Field(pattern=_PLATFORM_PATTERN)
    access_scope: AccessScope
    tos_status: ToSStatus
    legal_basis: LegalBasis
    collected_at: datetime
    metrics: AudienceMetrics
    topic_tags: tuple[str, ...] = Field(default_factory=tuple)
    personal_data_fields: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("collected_at")
    @classmethod
    def _normalize_collected_at(cls, value: datetime) -> datetime:
        return normalize_datetime(value)

    @field_validator("topic_tags", "personal_data_fields")
    @classmethod
    def _validate_tokens(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for item in value:
            token = item.strip()
            if token == "":
                raise ValueError("Список не должен содержать пустые значения")
            normalized.append(token)

        return tuple(normalized)


class AudienceProfile(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    profile_id: IdempotencyKey
    tenant_id: TenantId
    source_count: int = Field(ge=1)
    total_reach: int = Field(ge=0)
    engagement_rate: float = Field(ge=0, allow_inf_nan=False)
    topic_tags: tuple[str, ...] = Field(default_factory=tuple)
    public_sources_only: bool
    legal_basis: tuple[str, ...] = Field(default_factory=tuple)
    personal_data_fields: tuple[str, ...] = Field(default_factory=tuple)
    evidence_hash: AuditHash
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime) -> datetime:
        return normalize_datetime(value)


class AutoReplyRequest(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    trigger_id: IdempotencyKey
    platform: str = Field(pattern=_PLATFORM_PATTERN)
    recipient_ref: str = Field(min_length=1, max_length=256)
    template_key: str = Field(pattern=_TEMPLATE_KEY_PATTERN)
    risk_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    agent_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    estimated_recipients: int = Field(ge=1, le=10_000)
    created_at: datetime | None = None
    context: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None

        return normalize_datetime(value)


class AutoReplyDecision(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    trigger_id: IdempotencyKey
    platform: str
    template_key: str
    recipient_ref_hash: str
    status: AutoReplyStatus
    response_text: str
    estimated_recipients: int = Field(ge=1)
    policy_reasons: tuple[str, ...] = Field(default_factory=tuple)
    decided_at: datetime

    @field_validator("decided_at")
    @classmethod
    def _normalize_decided_at(cls, value: datetime) -> datetime:
        return normalize_datetime(value)


class CouncilThresholds(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    tenant_id: TenantId
    revision: int = Field(ge=1)
    max_autonomous_risk_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    min_agent_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    max_autonomous_recipients: int = Field(ge=1, le=10_000)
    allowed_template_keys: tuple[str, ...] = Field(default_factory=tuple)
    updated_by: SubjectId | None = None
    updated_at: datetime
    audit_hash: AuditHash = DEFAULT_THRESHOLD_AUDIT_HASH
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("updated_at")
    @classmethod
    def _normalize_updated_at(cls, value: datetime) -> datetime:
        return normalize_datetime(value)

    @field_validator("allowed_template_keys")
    @classmethod
    def _validate_template_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(
            dict.fromkeys(item.strip() for item in value if item.strip())
        )
        if len(normalized) == 0:
            raise ValueError("allowed_template_keys должен быть непустым")

        return normalized


class AgentRun(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    run_id: IdempotencyKey
    tenant_id: TenantId
    task_type: AgentTaskType
    status: AgentRunStatus
    policy_decision: PolicyDecision
    policy_revision: int = Field(ge=1)
    policy_reasons: tuple[str, ...] = Field(default_factory=tuple)
    audience_profile: AudienceProfile | None = None
    auto_reply: AutoReplyDecision | None = None
    audit_hash: AuditHash
    created_by: SubjectId
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def _normalize_datetime_field(cls, value: datetime) -> datetime:
        return normalize_datetime(value)


class AgentStatusResponse(SharedBaseModel):
    items: tuple[AgentRun, ...]


@dataclass(frozen=True, slots=True)
class ThresholdUpdateInput:
    max_autonomous_risk_score: float | None = None
    min_agent_confidence: float | None = None
    max_autonomous_recipients: int | None = None
    allowed_template_keys: tuple[str, ...] | None = None
    metadata: Mapping[str, JSONValue] | None = None


@dataclass(frozen=True, slots=True)
class AgentRunInput:
    task_type: AgentTaskType
    run_id: str | None = None
    event_id: str | None = None
    audience_sources: tuple[AudienceSource, ...] = ()
    auto_reply: AutoReplyRequest | None = None
    created_at: datetime | str | None = None


@dataclass(slots=True)
class InMemoryNeuroAgentRepository:
    _thresholds: dict[str, CouncilThresholds] = field(default_factory=dict)
    _runs: dict[tuple[str, str], AgentRun] = field(default_factory=dict)

    def get_thresholds(self, *, tenant_id: str) -> CouncilThresholds:
        thresholds = self._thresholds.get(tenant_id)
        if thresholds is not None:
            return thresholds

        default_thresholds = CouncilThresholds(
            tenant_id=tenant_id,
            revision=1,
            max_autonomous_risk_score=DEFAULT_MAX_AUTONOMOUS_RISK_SCORE,
            min_agent_confidence=DEFAULT_MIN_AGENT_CONFIDENCE,
            max_autonomous_recipients=DEFAULT_MAX_AUTONOMOUS_RECIPIENTS,
            allowed_template_keys=DEFAULT_ALLOWED_TEMPLATE_KEYS,
            updated_at=datetime(1970, 1, 1, tzinfo=UTC),
            metadata={"source": "default"},
        )
        self._thresholds[tenant_id] = default_thresholds
        return default_thresholds

    def save_thresholds(self, thresholds: CouncilThresholds) -> CouncilThresholds:
        self._thresholds[thresholds.tenant_id] = thresholds
        return thresholds

    def add_run(self, run: AgentRun) -> AgentRun:
        key = _run_key(run.tenant_id, run.run_id)
        if key in self._runs:
            raise AgentRunAlreadyExistsError(
                "Запуск агента с таким run_id уже существует для tenant"
            )

        self._runs[key] = run
        return run

    def list_runs(
        self,
        *,
        tenant_id: str,
        task_type: AgentTaskType | None = None,
    ) -> tuple[AgentRun, ...]:
        runs = (
            run
            for (record_tenant_id, _run_id), run in self._runs.items()
            if record_tenant_id == tenant_id
        )
        if task_type is not None:
            runs = (run for run in runs if run.task_type is task_type)

        return tuple(runs)


@dataclass(slots=True)
class NeuroAgentOrchestrator:
    publisher: EventPublisher = field(default_factory=InMemoryEventBus)
    repository: InMemoryNeuroAgentRepository = field(
        default_factory=InMemoryNeuroAgentRepository
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
    ) -> CouncilThresholds:
        existing = self.repository.get_thresholds(tenant_id=tenant_id)
        changed_at = normalize_datetime(updated_at or datetime.now(UTC))
        revision = existing.revision + 1
        allowed_template_keys = _coalesce(
            update.allowed_template_keys,
            existing.allowed_template_keys,
        )
        thresholds = CouncilThresholds(
            tenant_id=tenant_id,
            revision=revision,
            max_autonomous_risk_score=_coalesce(
                update.max_autonomous_risk_score,
                existing.max_autonomous_risk_score,
            ),
            min_agent_confidence=_coalesce(
                update.min_agent_confidence,
                existing.min_agent_confidence,
            ),
            max_autonomous_recipients=_coalesce(
                update.max_autonomous_recipients,
                existing.max_autonomous_recipients,
            ),
            allowed_template_keys=allowed_template_keys,
            updated_by=updated_by,
            updated_at=changed_at,
            metadata=dict(update.metadata or {}),
        )
        audit_record = self.audit_logger.record(
            event_type=THRESHOLDS_UPDATED_EVENT,
            tenant_id=tenant_id,
            metadata={
                "revision": thresholds.revision,
                "max_autonomous_risk_score": thresholds.max_autonomous_risk_score,
                "min_agent_confidence": thresholds.min_agent_confidence,
                "max_autonomous_recipients": thresholds.max_autonomous_recipients,
                "allowed_template_keys": list(thresholds.allowed_template_keys),
                "metadata": thresholds.metadata,
            },
            timestamp=changed_at,
            correlation_id=correlation_id,
            actor_hash=subject_ref_hash(tenant_id=tenant_id, subject_id=updated_by),
            source=NEURO_AGENT_SOURCE,
        )
        thresholds = thresholds.model_copy(
            update={"audit_hash": audit_record.audit_hash}
        )
        self.repository.save_thresholds(thresholds)

        await self.publisher.publish(
            EventEnvelope(
                event_id=event_id or _new_id("evt-neuro-thresholds-updated"),
                type=THRESHOLDS_UPDATED_EVENT,
                schema_version=NEURO_AGENT_SCHEMA_VERSION,
                tenant_id=tenant_id,
                source=NEURO_AGENT_SOURCE,
                correlation_id=correlation_id,
                occurred_at=changed_at,
                payload={
                    "revision": thresholds.revision,
                    "audit_hash": thresholds.audit_hash,
                    "max_autonomous_risk_score": (thresholds.max_autonomous_risk_score),
                    "min_agent_confidence": thresholds.min_agent_confidence,
                    "max_autonomous_recipients": (thresholds.max_autonomous_recipients),
                },
            )
        )
        return thresholds

    async def run_agent(
        self,
        *,
        tenant_id: str,
        created_by: str,
        correlation_id: str,
        run: AgentRunInput,
    ) -> AgentRun:
        created_at = normalize_datetime(run.created_at or datetime.now(UTC))
        thresholds = self.repository.get_thresholds(tenant_id=tenant_id)
        resolved_run_id = run.run_id or _new_id("run")

        if run.task_type is AgentTaskType.AUDIENCE_ANALYSIS:
            created_run = await self._run_audience_analysis(
                tenant_id=tenant_id,
                created_by=created_by,
                correlation_id=correlation_id,
                run_id=resolved_run_id,
                event_id=run.event_id,
                sources=run.audience_sources,
                thresholds=thresholds,
                created_at=created_at,
            )
        elif run.task_type is AgentTaskType.ENGAGEMENT_AUTO_REPLY:
            if run.auto_reply is None:
                raise ValueError("auto_reply обязателен для engagement_auto_reply")
            created_run = await self._run_auto_reply(
                tenant_id=tenant_id,
                created_by=created_by,
                correlation_id=correlation_id,
                run_id=resolved_run_id,
                event_id=run.event_id,
                request=run.auto_reply,
                thresholds=thresholds,
                created_at=created_at,
            )
        else:
            raise ValueError("task_type не поддерживается")

        self.repository.add_run(created_run)
        return created_run

    def list_runs(
        self,
        *,
        tenant_id: str,
        task_type: AgentTaskType | None = None,
    ) -> tuple[AgentRun, ...]:
        return self.repository.list_runs(tenant_id=tenant_id, task_type=task_type)

    def get_thresholds(self, *, tenant_id: str) -> CouncilThresholds:
        return self.repository.get_thresholds(tenant_id=tenant_id)

    async def _run_audience_analysis(
        self,
        *,
        tenant_id: str,
        created_by: str,
        correlation_id: str,
        run_id: str,
        event_id: str | None,
        sources: tuple[AudienceSource, ...],
        thresholds: CouncilThresholds,
        created_at: datetime,
    ) -> AgentRun:
        if len(sources) == 0:
            raise ValueError("audience_sources должен содержать хотя бы один источник")

        profile = build_audience_profile(
            tenant_id=tenant_id,
            sources=sources,
            created_at=created_at,
            profile_id=f"profile-{run_id}",
        )
        audit_record = self.audit_logger.record(
            event_type=AUDIENCE_PROFILE_CREATED_EVENT,
            tenant_id=tenant_id,
            metadata={
                "run_id": run_id,
                "profile_id": profile.profile_id,
                "source_count": profile.source_count,
                "total_reach": profile.total_reach,
                "engagement_rate": profile.engagement_rate,
                "topic_tags": list(profile.topic_tags),
                "public_sources_only": profile.public_sources_only,
                "legal_basis": list(profile.legal_basis),
                "evidence_hash": profile.evidence_hash,
            },
            timestamp=created_at,
            correlation_id=correlation_id,
            actor_hash=subject_ref_hash(tenant_id=tenant_id, subject_id=created_by),
            source=NEURO_AGENT_SOURCE,
        )
        agent_run = AgentRun(
            run_id=run_id,
            tenant_id=tenant_id,
            task_type=AgentTaskType.AUDIENCE_ANALYSIS,
            status=AgentRunStatus.COMPLETED,
            policy_decision=PolicyDecision.ALLOW,
            policy_revision=thresholds.revision,
            audience_profile=profile,
            audit_hash=audit_record.audit_hash,
            created_by=created_by,
            created_at=created_at,
            updated_at=created_at,
        )
        await self.publisher.publish(
            EventEnvelope(
                event_id=event_id or _new_id("evt-audience-profile-created"),
                type=AUDIENCE_PROFILE_CREATED_EVENT,
                schema_version=NEURO_AGENT_SCHEMA_VERSION,
                tenant_id=tenant_id,
                source=NEURO_AGENT_SOURCE,
                correlation_id=correlation_id,
                occurred_at=created_at,
                payload={
                    "run_id": agent_run.run_id,
                    "profile_id": profile.profile_id,
                    "source_count": profile.source_count,
                    "total_reach": profile.total_reach,
                    "audit_hash": agent_run.audit_hash,
                },
            )
        )
        return agent_run

    async def _run_auto_reply(
        self,
        *,
        tenant_id: str,
        created_by: str,
        correlation_id: str,
        run_id: str,
        event_id: str | None,
        request: AutoReplyRequest,
        thresholds: CouncilThresholds,
        created_at: datetime,
    ) -> AgentRun:
        decided_at = normalize_datetime(request.created_at or created_at)
        reasons = auto_reply_policy_reasons(
            request=request,
            thresholds=thresholds,
        )
        policy_decision = PolicyDecision.ESCALATE if reasons else PolicyDecision.ALLOW
        run_status = (
            AgentRunStatus.NEEDS_COUNCIL_REVIEW
            if policy_decision is PolicyDecision.ESCALATE
            else AgentRunStatus.COMPLETED
        )
        reply_status = (
            AutoReplyStatus.NEEDS_COUNCIL_REVIEW
            if policy_decision is PolicyDecision.ESCALATE
            else AutoReplyStatus.SENT
        )
        event_type = (
            AUTO_REPLY_ESCALATED_EVENT
            if policy_decision is PolicyDecision.ESCALATE
            else AUTO_REPLY_SENT_EVENT
        )
        recipient_ref_hash = subject_ref_hash(
            tenant_id=tenant_id,
            subject_id=request.recipient_ref,
        )
        decision = AutoReplyDecision(
            trigger_id=request.trigger_id,
            platform=request.platform,
            template_key=request.template_key,
            recipient_ref_hash=recipient_ref_hash,
            status=reply_status,
            response_text=render_auto_reply(
                template_key=request.template_key,
                context=request.context,
            ),
            estimated_recipients=request.estimated_recipients,
            policy_reasons=reasons,
            decided_at=decided_at,
        )
        audit_record = self.audit_logger.record(
            event_type=event_type,
            tenant_id=tenant_id,
            metadata={
                "run_id": run_id,
                "trigger_id": request.trigger_id,
                "platform": request.platform,
                "template_key": request.template_key,
                "recipient_ref_hash": recipient_ref_hash,
                "status": reply_status.value,
                "policy_decision": policy_decision.value,
                "policy_revision": thresholds.revision,
                "policy_reasons": list(reasons),
                "risk_score": request.risk_score,
                "agent_confidence": request.agent_confidence,
                "estimated_recipients": request.estimated_recipients,
            },
            timestamp=decided_at,
            correlation_id=correlation_id,
            actor_hash=subject_ref_hash(tenant_id=tenant_id, subject_id=created_by),
            source=NEURO_AGENT_SOURCE,
        )
        agent_run = AgentRun(
            run_id=run_id,
            tenant_id=tenant_id,
            task_type=AgentTaskType.ENGAGEMENT_AUTO_REPLY,
            status=run_status,
            policy_decision=policy_decision,
            policy_revision=thresholds.revision,
            policy_reasons=reasons,
            auto_reply=decision,
            audit_hash=audit_record.audit_hash,
            created_by=created_by,
            created_at=decided_at,
            updated_at=decided_at,
        )
        await self.publisher.publish(
            EventEnvelope(
                event_id=event_id or _new_id("evt-auto-reply"),
                type=event_type,
                schema_version=NEURO_AGENT_SCHEMA_VERSION,
                tenant_id=tenant_id,
                source=NEURO_AGENT_SOURCE,
                correlation_id=correlation_id,
                occurred_at=decided_at,
                payload={
                    "run_id": agent_run.run_id,
                    "trigger_id": request.trigger_id,
                    "status": reply_status.value,
                    "policy_decision": policy_decision.value,
                    "policy_revision": thresholds.revision,
                    "policy_reasons": list(reasons),
                    "recipient_ref_hash": recipient_ref_hash,
                    "audit_hash": agent_run.audit_hash,
                },
            )
        )
        return agent_run


def build_audience_profile(
    *,
    tenant_id: str,
    sources: tuple[AudienceSource, ...],
    created_at: datetime,
    profile_id: str,
) -> AudienceProfile:
    reasons = audience_source_policy_reasons(sources)
    if reasons:
        raise PdnScopeViolationError(reasons)

    total_reach = sum(source.metrics.reach() for source in sources)
    engagement_count = sum(source.metrics.engagement_count() for source in sources)
    engagement_rate = (
        0.0 if total_reach == 0 else round(engagement_count / total_reach, 4)
    )
    topic_tags = tuple(sorted({tag for source in sources for tag in source.topic_tags}))
    legal_basis = tuple(sorted({source.legal_basis.value for source in sources}))
    evidence_hash = _hash_json(
        {
            "tenant_id": tenant_id,
            "sources": [
                {
                    "source_id": source.source_id,
                    "platform": source.platform,
                    "metrics": source.metrics.model_dump(mode="json"),
                    "topic_tags": list(source.topic_tags),
                    "legal_basis": source.legal_basis.value,
                }
                for source in sources
            ],
        }
    )
    return AudienceProfile(
        profile_id=profile_id,
        tenant_id=tenant_id,
        source_count=len(sources),
        total_reach=total_reach,
        engagement_rate=engagement_rate,
        topic_tags=topic_tags,
        public_sources_only=True,
        legal_basis=legal_basis,
        personal_data_fields=(),
        evidence_hash=evidence_hash,
        created_at=created_at,
    )


def audience_source_policy_reasons(
    sources: tuple[AudienceSource, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    for source in sources:
        if source.access_scope is not AccessScope.PUBLIC:
            reasons.append("non_public_source")
        if source.tos_status is not ToSStatus.ALLOWED:
            reasons.append("tos_not_allowed")
        if source.legal_basis is LegalBasis.MISSING:
            reasons.append("legal_basis_missing")
        if len(source.personal_data_fields) > 0:
            reasons.append("personal_data_detected")
        if source.platform in _BLOCKED_META_PLATFORMS:
            reasons.append("blocked_platform")

    return tuple(dict.fromkeys(reasons))


def auto_reply_policy_reasons(
    *,
    request: AutoReplyRequest,
    thresholds: CouncilThresholds,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if request.risk_score > thresholds.max_autonomous_risk_score:
        reasons.append("risk_score_above_threshold")
    if request.agent_confidence < thresholds.min_agent_confidence:
        reasons.append("confidence_below_threshold")
    if request.estimated_recipients > thresholds.max_autonomous_recipients:
        reasons.append("recipient_limit_exceeded")
    if request.template_key not in thresholds.allowed_template_keys:
        reasons.append("template_not_allowed")
    if request.platform in _BLOCKED_META_PLATFORMS:
        reasons.append("blocked_platform")

    return tuple(reasons)


def render_auto_reply(*, template_key: str, context: Mapping[str, JSONValue]) -> str:
    topic = context.get("topic")
    topic_hint = f" Тема: {topic}." if isinstance(topic, str) and topic else ""
    templates = {
        "welcome": (
            "Спасибо за интерес к НМЦ. Подскажите, какой формат участия вам ближе?"
        ),
        "faq_basic": (
            "Коротко: участие строится на правилах сообщества, баллах вклада "
            "и решениях Совета."
        ),
        "participation_rules": (
            "Правила участия и спорные вопросы проходят через утвержденные "
            "процедуры Совета."
        ),
    }
    return (
        templates.get(template_key, "Спасибо за обращение. Передали вопрос Совету.")
        + topic_hint
    )


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


def _hash_json(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _coalesce[T](value: T | None, fallback: T) -> T:
    if value is None:
        return fallback

    return value


def _run_key(tenant_id: str, run_id: str) -> tuple[str, str]:
    return tenant_id, run_id


def _new_id(prefix: str) -> IdempotencyKey:
    return f"{prefix}-{uuid4()}"
