from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import cast
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
from libs.shared.tenant import TenantContext
from libs.shared.vector import (
    InMemoryTenantVectorStore,
    TenantVectorStore,
    VectorRecord,
    VectorSearchResult,
)

NEURO_AGENT_SOURCE = "neuro-agent-orchestrator"
NEURO_AGENT_SCHEMA_VERSION = "1.0"
THRESHOLDS_UPDATED_EVENT = "neuro_agent.thresholds.updated"
AUDIENCE_PROFILE_CREATED_EVENT = "neuro_agent.audience_profile.created"
AUTO_REPLY_SENT_EVENT = "neuro_agent.auto_reply.sent"
AUTO_REPLY_ESCALATED_EVENT = "neuro_agent.auto_reply.escalated"
CONTENT_HYGIENE_PASSED_EVENT = "neuro_agent.content_hygiene.passed"
CONTENT_HYGIENE_FLAGGED_EVENT = "neuro_agent.content_hygiene.flagged"
PUBLICATION_ANALYTICS_CREATED_EVENT = "neuro_agent.publication_analytics.created"
RAG_DOCUMENTS_UPSERTED_EVENT = "neuro_agent.rag.documents.upserted"
RAG_QUERY_COMPLETED_EVENT = "neuro_agent.rag.query.completed"
DEEP_RESEARCH_DRAFT_CREATED_EVENT = "neuro_agent.deep_research.draft.created"
CONTENT_AGENT_ACTION_PROPOSED_EVENT = "neuro_agent.content_agent.action_proposed"
DECISION_EXPLANATION_AUDIT_SCOPE = "neuro_agent.decision_explanation"

DEFAULT_MAX_AUTONOMOUS_RISK_SCORE = 0.45
DEFAULT_MIN_AGENT_CONFIDENCE = 0.75
DEFAULT_MAX_AUTONOMOUS_RECIPIENTS = 5
DEFAULT_MIN_CONTENT_QUALITY_SCORE = 0.7
DEFAULT_ALLOWED_TEMPLATE_KEYS = ("welcome", "faq_basic", "participation_rules")
DEFAULT_THRESHOLD_AUDIT_HASH = "0" * 64
DEFAULT_DECISION_EXPLANATION_AUDIT_HASH = "0" * 64
RAG_VECTOR_DOMAIN = "agentic_rag"
RAG_EMBEDDING_DIMENSIONS = 32

_TEMPLATE_KEY_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
_PLATFORM_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"
_BLOCKED_META_PLATFORMS = frozenset({"facebook", "instagram"})
_UNSAFE_CONTENT_KEYWORDS = frozenset(
    {
        "18+",
        "казино",
        "наркот",
        "мошен",
        "насили",
        "экстрем",
        "ставк",
    }
)
_WORD_RE = re.compile(r"[\wА-Яа-яЁё]+", flags=re.UNICODE)
_REPEATED_PUNCTUATION_RE = re.compile(r"[!?]{3,}")


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
    CONTENT_HYGIENE = "content_hygiene"
    PUBLICATION_OPTIMIZATION = "publication_optimization"
    AGENTIC_RAG = "agentic_rag"
    DEEP_RESEARCH = "deep_research"
    CONTENT_AGENT_ACTION = "content_agent_action"


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


class ContentHygieneStatus(StrEnum):
    PASSED = "passed"
    FLAGGED = "flagged"


class OptimizationRecommendationStatus(StrEnum):
    PROPOSED = "proposed"
    NEEDS_COUNCIL_REVIEW = "needs_council_review"


class DeepResearchDraftStatus(StrEnum):
    DRAFTED = "drafted"


class ContentAgentApprovalStatus(StrEnum):
    AWAITING_HUMAN_APPROVAL = "awaiting_human_approval"


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


class ContentHygieneRequest(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    content_id: IdempotencyKey
    platform: str = Field(pattern=_PLATFORM_PATTERN)
    content_text: str = Field(min_length=1, max_length=20_000)
    author_ref: str | None = Field(default=None, min_length=1, max_length=256)
    created_at: datetime | None = None
    context: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None

        return normalize_datetime(value)


class ContentHygieneAssessment(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    content_id: IdempotencyKey
    platform: str
    content_hash: str
    author_ref_hash: str | None = None
    status: ContentHygieneStatus
    quality_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    safety_risk_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    flags: tuple[str, ...] = Field(default_factory=tuple)
    policy_reasons: tuple[str, ...] = Field(default_factory=tuple)
    evidence_hash: AuditHash
    assessed_at: datetime

    @field_validator("assessed_at")
    @classmethod
    def _normalize_assessed_at(cls, value: datetime) -> datetime:
        return normalize_datetime(value)


class PublicationMetrics(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    impressions: int = Field(default=0, ge=0)
    reach: int = Field(default=0, ge=0)
    clicks: int = Field(default=0, ge=0)
    reactions: int = Field(default=0, ge=0)
    comments: int = Field(default=0, ge=0)
    shares: int = Field(default=0, ge=0)
    conversions: int = Field(default=0, ge=0)

    def engagement_count(self) -> int:
        return self.reactions + self.comments + self.shares

    def engagement_base(self) -> int:
        return self.reach if self.reach > 0 else self.impressions


class PublicationOptimizationRequest(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    publication_id: IdempotencyKey
    platform: str = Field(pattern=_PLATFORM_PATTERN)
    published_at: datetime
    metrics: PublicationMetrics
    topic_tags: tuple[str, ...] = Field(default_factory=tuple)
    agent_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    recommendation_risk_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    created_at: datetime | None = None

    @field_validator("published_at")
    @classmethod
    def _normalize_published_at(cls, value: datetime) -> datetime:
        return normalize_datetime(value)

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None

        return normalize_datetime(value)

    @field_validator("topic_tags")
    @classmethod
    def _validate_topic_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return normalize_token_tuple(value)


class OptimizationRecommendation(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    recommendation_id: IdempotencyKey
    action: str = Field(pattern=_TEMPLATE_KEY_PATTERN)
    rationale_code: str = Field(pattern=_TEMPLATE_KEY_PATTERN)
    expected_metric: str = Field(pattern=_TEMPLATE_KEY_PATTERN)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    risk_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    status: OptimizationRecommendationStatus
    auto_applied: bool = False
    requires_human_approval: bool = True
    policy_reasons: tuple[str, ...] = Field(default_factory=tuple)


class PublicationAnalyticsReport(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    publication_id: IdempotencyKey
    platform: str
    published_at: datetime
    impressions: int = Field(ge=0)
    reach: int = Field(ge=0)
    engagement_rate: float = Field(ge=0, allow_inf_nan=False)
    click_through_rate: float = Field(ge=0, allow_inf_nan=False)
    conversion_rate: float = Field(ge=0, allow_inf_nan=False)
    share_rate: float = Field(ge=0, allow_inf_nan=False)
    performance_band: str = Field(pattern=_TEMPLATE_KEY_PATTERN)
    topic_tags: tuple[str, ...] = Field(default_factory=tuple)
    recommendations: tuple[OptimizationRecommendation, ...] = Field(
        default_factory=tuple
    )
    evidence_hash: AuditHash
    created_at: datetime

    @field_validator("published_at", "created_at")
    @classmethod
    def _normalize_datetime_field(cls, value: datetime) -> datetime:
        return normalize_datetime(value)


class RagDocumentInput(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    document_id: IdempotencyKey
    content: str = Field(min_length=1, max_length=40_000)
    source_type: str = Field(pattern=_TEMPLATE_KEY_PATTERN)
    source_ref: str | None = Field(default=None, min_length=1, max_length=512)
    topic_tags: tuple[str, ...] = Field(default_factory=tuple)
    created_at: datetime | None = None

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None

        return normalize_datetime(value)

    @field_validator("topic_tags")
    @classmethod
    def _validate_topic_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return normalize_token_tuple(value)


class RagDocumentReference(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    document_id: IdempotencyKey
    source_type: str
    source_ref_hash: str | None = None
    content_hash: str
    topic_tags: tuple[str, ...] = Field(default_factory=tuple)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime) -> datetime:
        return normalize_datetime(value)


class RagDocumentsUpsertResult(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    tenant_id: TenantId
    upserted_count: int = Field(ge=1)
    documents: tuple[RagDocumentReference, ...]
    audit_hash: AuditHash
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def _normalize_updated_at(cls, value: datetime) -> datetime:
        return normalize_datetime(value)


class AgenticRagQueryRequest(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    query_id: IdempotencyKey
    query_text: str = Field(min_length=1, max_length=4_000)
    limit: int = Field(default=5, ge=1, le=10)
    source_type: str | None = Field(default=None, pattern=_TEMPLATE_KEY_PATTERN)
    topic_tags: tuple[str, ...] = Field(default_factory=tuple)
    created_at: datetime | None = None

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None

        return normalize_datetime(value)

    @field_validator("topic_tags")
    @classmethod
    def _validate_topic_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return normalize_token_tuple(value)


class AgenticRagContextItem(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    document_id: IdempotencyKey
    source_type: str
    source_ref_hash: str | None = None
    content_hash: str
    excerpt: str
    distance: float = Field(ge=0, allow_inf_nan=False)
    relevance_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    topic_tags: tuple[str, ...] = Field(default_factory=tuple)


class AgenticRagAnswer(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    query_id: IdempotencyKey
    tenant_id: TenantId
    query_hash: str
    answer_text: str
    context_items: tuple[AgenticRagContextItem, ...]
    retrieval_count: int = Field(ge=0)
    evidence_hash: AuditHash
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime) -> datetime:
        return normalize_datetime(value)


class DeepResearchRequest(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    research_id: IdempotencyKey
    question: str = Field(min_length=1, max_length=4_000)
    query_text: str | None = Field(default=None, min_length=1, max_length=4_000)
    limit: int = Field(default=5, ge=1, le=10)
    topic_tags: tuple[str, ...] = Field(default_factory=tuple)
    created_at: datetime | None = None

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None

        return normalize_datetime(value)

    @field_validator("topic_tags")
    @classmethod
    def _validate_topic_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return normalize_token_tuple(value)


class DeepResearchCitation(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    document_id: IdempotencyKey
    source_type: str
    source_ref_hash: str | None = None
    content_hash: str
    excerpt: str


class DeepResearchDraft(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    research_id: IdempotencyKey
    question_hash: str
    title: str
    draft_text: str
    outline: tuple[str, ...]
    citations: tuple[DeepResearchCitation, ...]
    citation_count: int = Field(ge=0)
    draft_status: DeepResearchDraftStatus
    requires_human_review: bool = True
    evidence_hash: AuditHash
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime) -> datetime:
        return normalize_datetime(value)


class ContentAgentProposedActionInput(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    action_type: str = Field(pattern=_TEMPLATE_KEY_PATTERN)
    target_ref: str = Field(min_length=1, max_length=512)
    summary: str = Field(min_length=1, max_length=512)


class ContentAgentActionRequest(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    action_id: IdempotencyKey
    workspace_ref: str = Field(min_length=1, max_length=512)
    goal: str = Field(min_length=1, max_length=2_000)
    proposed_actions: tuple[ContentAgentProposedActionInput, ...] = Field(
        min_length=1,
        max_length=20,
    )
    risk_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    agent_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    created_at: datetime | None = None

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None

        return normalize_datetime(value)


class ContentAgentActionStep(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    action_type: str
    target_ref_hash: str
    summary: str


class ContentAgentActionPlan(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    action_id: IdempotencyKey
    workspace_ref_hash: str
    goal: str
    actions: tuple[ContentAgentActionStep, ...]
    risk_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    agent_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    approval_status: ContentAgentApprovalStatus
    requires_human_approval: bool = True
    auto_executed: bool = False
    policy_reasons: tuple[str, ...] = Field(default_factory=tuple)
    evidence_hash: AuditHash
    proposed_at: datetime

    @field_validator("proposed_at")
    @classmethod
    def _normalize_proposed_at(cls, value: datetime) -> datetime:
        return normalize_datetime(value)


class DecisionExplanation(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    explanation_id: IdempotencyKey
    tenant_id: TenantId
    run_id: IdempotencyKey
    task_type: AgentTaskType
    status: AgentRunStatus
    policy_decision: PolicyDecision
    policy_revision: int = Field(ge=1)
    summary: str = Field(min_length=1, max_length=1_000)
    reason_codes: tuple[str, ...] = Field(default_factory=tuple)
    input_facts: dict[str, JSONValue] = Field(default_factory=dict)
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple)
    action_refs: tuple[str, ...] = Field(default_factory=tuple)
    explanation_hash: AuditHash
    audit_hash: AuditHash = DEFAULT_DECISION_EXPLANATION_AUDIT_HASH
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime) -> datetime:
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
    min_content_quality_score: float = Field(ge=0, le=1, allow_inf_nan=False)
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
    content_hygiene: ContentHygieneAssessment | None = None
    publication_analytics: PublicationAnalyticsReport | None = None
    rag_answer: AgenticRagAnswer | None = None
    deep_research_draft: DeepResearchDraft | None = None
    content_agent_action: ContentAgentActionPlan | None = None
    decision_explanation: DecisionExplanation
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


class DecisionExplanationListResponse(SharedBaseModel):
    items: tuple[DecisionExplanation, ...]


@dataclass(frozen=True, slots=True)
class ThresholdUpdateInput:
    max_autonomous_risk_score: float | None = None
    min_agent_confidence: float | None = None
    max_autonomous_recipients: int | None = None
    min_content_quality_score: float | None = None
    allowed_template_keys: tuple[str, ...] | None = None
    metadata: Mapping[str, JSONValue] | None = None


@dataclass(frozen=True, slots=True)
class AgentRunInput:
    task_type: AgentTaskType
    run_id: str | None = None
    event_id: str | None = None
    audience_sources: tuple[AudienceSource, ...] = ()
    auto_reply: AutoReplyRequest | None = None
    content_hygiene: ContentHygieneRequest | None = None
    publication_optimization: PublicationOptimizationRequest | None = None
    rag_query: AgenticRagQueryRequest | None = None
    deep_research: DeepResearchRequest | None = None
    content_agent_action: ContentAgentActionRequest | None = None
    created_at: datetime | str | None = None


@dataclass(slots=True)
class InMemoryNeuroAgentRepository:
    _thresholds: dict[str, CouncilThresholds] = field(default_factory=dict)
    _runs: dict[tuple[str, str], AgentRun] = field(default_factory=dict)
    _decision_explanations: dict[tuple[str, str], DecisionExplanation] = field(
        default_factory=dict
    )

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
            min_content_quality_score=DEFAULT_MIN_CONTENT_QUALITY_SCORE,
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
        self._decision_explanations[key] = run.decision_explanation
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

    def list_decision_explanations(
        self,
        *,
        tenant_id: str,
        task_type: AgentTaskType | None = None,
    ) -> tuple[DecisionExplanation, ...]:
        explanations = (
            explanation
            for (
                record_tenant_id,
                _run_id,
            ), explanation in self._decision_explanations.items()
            if record_tenant_id == tenant_id
        )
        if task_type is not None:
            explanations = (
                explanation
                for explanation in explanations
                if explanation.task_type is task_type
            )

        return tuple(explanations)


@dataclass(slots=True)
class NeuroAgentOrchestrator:
    publisher: EventPublisher = field(default_factory=InMemoryEventBus)
    repository: InMemoryNeuroAgentRepository = field(
        default_factory=InMemoryNeuroAgentRepository
    )
    audit_logger: AuditLogger = field(default_factory=AuditLogger)
    vector_store: TenantVectorStore = field(default_factory=InMemoryTenantVectorStore)

    async def upsert_rag_documents(
        self,
        *,
        tenant_id: str,
        updated_by: str,
        correlation_id: str,
        documents: tuple[RagDocumentInput, ...],
        updated_at: datetime | str | None = None,
        event_id: str | None = None,
    ) -> RagDocumentsUpsertResult:
        if len(documents) == 0:
            raise ValueError("documents должен содержать хотя бы один документ")

        changed_at = normalize_datetime(updated_at or datetime.now(UTC))
        context = _tenant_context(
            tenant_id=tenant_id,
            subject=updated_by,
            correlation_id=correlation_id,
        )
        references = tuple(
            build_rag_document_reference(
                tenant_id=tenant_id,
                document=document,
                created_at=normalize_datetime(document.created_at or changed_at),
            )
            for document in documents
        )
        records = tuple(
            rag_document_vector_record(document=document, reference=reference)
            for document, reference in zip(documents, references, strict=True)
        )
        await self.vector_store.upsert(
            RAG_VECTOR_DOMAIN,
            records,
            context=context,
        )
        audit_record = self.audit_logger.record(
            event_type=RAG_DOCUMENTS_UPSERTED_EVENT,
            tenant_id=tenant_id,
            metadata={
                "document_ids": [reference.document_id for reference in references],
                "source_types": [reference.source_type for reference in references],
                "source_ref_hashes": [
                    reference.source_ref_hash for reference in references
                ],
                "content_hashes": [reference.content_hash for reference in references],
                "upserted_count": len(references),
            },
            timestamp=changed_at,
            correlation_id=correlation_id,
            actor_hash=subject_ref_hash(tenant_id=tenant_id, subject_id=updated_by),
            source=NEURO_AGENT_SOURCE,
        )
        result = RagDocumentsUpsertResult(
            tenant_id=tenant_id,
            upserted_count=len(references),
            documents=references,
            audit_hash=audit_record.audit_hash,
            updated_at=changed_at,
        )
        await self.publisher.publish(
            EventEnvelope(
                event_id=event_id or _new_id("evt-rag-documents-upserted"),
                type=RAG_DOCUMENTS_UPSERTED_EVENT,
                schema_version=NEURO_AGENT_SCHEMA_VERSION,
                tenant_id=tenant_id,
                source=NEURO_AGENT_SOURCE,
                correlation_id=correlation_id,
                occurred_at=changed_at,
                payload={
                    "document_ids": [reference.document_id for reference in references],
                    "source_types": [reference.source_type for reference in references],
                    "content_hashes": [
                        reference.content_hash for reference in references
                    ],
                    "upserted_count": result.upserted_count,
                    "audit_hash": result.audit_hash,
                },
            )
        )
        return result

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
            min_content_quality_score=_coalesce(
                update.min_content_quality_score,
                existing.min_content_quality_score,
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
                "min_content_quality_score": thresholds.min_content_quality_score,
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
                    "min_content_quality_score": (thresholds.min_content_quality_score),
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
        elif run.task_type is AgentTaskType.CONTENT_HYGIENE:
            if run.content_hygiene is None:
                raise ValueError("content_hygiene обязателен для content_hygiene")
            created_run = await self._run_content_hygiene(
                tenant_id=tenant_id,
                created_by=created_by,
                correlation_id=correlation_id,
                run_id=resolved_run_id,
                event_id=run.event_id,
                request=run.content_hygiene,
                thresholds=thresholds,
                created_at=created_at,
            )
        elif run.task_type is AgentTaskType.PUBLICATION_OPTIMIZATION:
            if run.publication_optimization is None:
                raise ValueError(
                    "publication_optimization обязателен для publication_optimization"
                )
            created_run = await self._run_publication_optimization(
                tenant_id=tenant_id,
                created_by=created_by,
                correlation_id=correlation_id,
                run_id=resolved_run_id,
                event_id=run.event_id,
                request=run.publication_optimization,
                thresholds=thresholds,
                created_at=created_at,
            )
        elif run.task_type is AgentTaskType.AGENTIC_RAG:
            if run.rag_query is None:
                raise ValueError("rag_query обязателен для agentic_rag")
            created_run = await self._run_agentic_rag(
                tenant_id=tenant_id,
                created_by=created_by,
                correlation_id=correlation_id,
                run_id=resolved_run_id,
                event_id=run.event_id,
                request=run.rag_query,
                thresholds=thresholds,
                created_at=created_at,
            )
        elif run.task_type is AgentTaskType.DEEP_RESEARCH:
            if run.deep_research is None:
                raise ValueError("deep_research обязателен для deep_research")
            created_run = await self._run_deep_research(
                tenant_id=tenant_id,
                created_by=created_by,
                correlation_id=correlation_id,
                run_id=resolved_run_id,
                event_id=run.event_id,
                request=run.deep_research,
                thresholds=thresholds,
                created_at=created_at,
            )
        elif run.task_type is AgentTaskType.CONTENT_AGENT_ACTION:
            if run.content_agent_action is None:
                raise ValueError(
                    "content_agent_action обязателен для content_agent_action"
                )
            created_run = await self._run_content_agent_action(
                tenant_id=tenant_id,
                created_by=created_by,
                correlation_id=correlation_id,
                run_id=resolved_run_id,
                event_id=run.event_id,
                request=run.content_agent_action,
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

    def list_decision_explanations(
        self,
        *,
        tenant_id: str,
        task_type: AgentTaskType | None = None,
    ) -> tuple[DecisionExplanation, ...]:
        return self.repository.list_decision_explanations(
            tenant_id=tenant_id,
            task_type=task_type,
        )

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
        explanation = build_decision_explanation(
            tenant_id=tenant_id,
            run_id=run_id,
            task_type=AgentTaskType.AUDIENCE_ANALYSIS,
            status=AgentRunStatus.COMPLETED,
            policy_decision=PolicyDecision.ALLOW,
            policy_revision=thresholds.revision,
            reason_codes=(),
            input_facts={
                "source_count": profile.source_count,
                "total_reach": profile.total_reach,
                "engagement_rate": profile.engagement_rate,
                "public_sources_only": profile.public_sources_only,
                "legal_basis": list(profile.legal_basis),
            },
            evidence_refs=(f"audience_evidence:{profile.evidence_hash}",),
            action_refs=(f"audience_profile:{profile.profile_id}",),
            created_at=created_at,
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
                **decision_explanation_audit_metadata(explanation),
            },
            timestamp=created_at,
            correlation_id=correlation_id,
            actor_hash=subject_ref_hash(tenant_id=tenant_id, subject_id=created_by),
            source=NEURO_AGENT_SOURCE,
        )
        explanation = explanation.model_copy(
            update={"audit_hash": audit_record.audit_hash}
        )
        agent_run = AgentRun(
            run_id=run_id,
            tenant_id=tenant_id,
            task_type=AgentTaskType.AUDIENCE_ANALYSIS,
            status=AgentRunStatus.COMPLETED,
            policy_decision=PolicyDecision.ALLOW,
            policy_revision=thresholds.revision,
            audience_profile=profile,
            decision_explanation=explanation,
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
                    **decision_explanation_event_payload(explanation),
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
        explanation = build_decision_explanation(
            tenant_id=tenant_id,
            run_id=run_id,
            task_type=AgentTaskType.ENGAGEMENT_AUTO_REPLY,
            status=run_status,
            policy_decision=policy_decision,
            policy_revision=thresholds.revision,
            reason_codes=reasons,
            input_facts={
                "platform": request.platform,
                "template_key": request.template_key,
                "risk_score": request.risk_score,
                "agent_confidence": request.agent_confidence,
                "estimated_recipients": request.estimated_recipients,
                "max_autonomous_risk_score": thresholds.max_autonomous_risk_score,
                "min_agent_confidence": thresholds.min_agent_confidence,
                "max_autonomous_recipients": thresholds.max_autonomous_recipients,
            },
            evidence_refs=(f"recipient_ref_hash:{recipient_ref_hash}",),
            action_refs=(f"auto_reply:{request.trigger_id}",),
            created_at=decided_at,
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
                **decision_explanation_audit_metadata(explanation),
            },
            timestamp=decided_at,
            correlation_id=correlation_id,
            actor_hash=subject_ref_hash(tenant_id=tenant_id, subject_id=created_by),
            source=NEURO_AGENT_SOURCE,
        )
        explanation = explanation.model_copy(
            update={"audit_hash": audit_record.audit_hash}
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
            decision_explanation=explanation,
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
                    **decision_explanation_event_payload(explanation),
                },
            )
        )
        return agent_run

    async def _run_content_hygiene(
        self,
        *,
        tenant_id: str,
        created_by: str,
        correlation_id: str,
        run_id: str,
        event_id: str | None,
        request: ContentHygieneRequest,
        thresholds: CouncilThresholds,
        created_at: datetime,
    ) -> AgentRun:
        assessed_at = normalize_datetime(request.created_at or created_at)
        assessment = assess_content_hygiene(
            tenant_id=tenant_id,
            request=request,
            thresholds=thresholds,
            assessed_at=assessed_at,
        )
        policy_decision = (
            PolicyDecision.ESCALATE
            if assessment.policy_reasons
            else PolicyDecision.ALLOW
        )
        run_status = (
            AgentRunStatus.NEEDS_COUNCIL_REVIEW
            if policy_decision is PolicyDecision.ESCALATE
            else AgentRunStatus.COMPLETED
        )
        event_type = (
            CONTENT_HYGIENE_FLAGGED_EVENT
            if assessment.status is ContentHygieneStatus.FLAGGED
            else CONTENT_HYGIENE_PASSED_EVENT
        )
        explanation = build_decision_explanation(
            tenant_id=tenant_id,
            run_id=run_id,
            task_type=AgentTaskType.CONTENT_HYGIENE,
            status=run_status,
            policy_decision=policy_decision,
            policy_revision=thresholds.revision,
            reason_codes=assessment.policy_reasons,
            input_facts={
                "content_id": request.content_id,
                "platform": request.platform,
                "quality_score": assessment.quality_score,
                "safety_risk_score": assessment.safety_risk_score,
                "flags": list(assessment.flags),
                "min_content_quality_score": thresholds.min_content_quality_score,
                "max_autonomous_risk_score": thresholds.max_autonomous_risk_score,
            },
            evidence_refs=(
                f"content_hash:{assessment.content_hash}",
                f"evidence:{assessment.evidence_hash}",
            ),
            action_refs=(f"content_hygiene:{request.content_id}",),
            created_at=assessment.assessed_at,
        )
        audit_record = self.audit_logger.record(
            event_type=event_type,
            tenant_id=tenant_id,
            metadata={
                "run_id": run_id,
                "content_id": request.content_id,
                "platform": request.platform,
                "content_hash": assessment.content_hash,
                "author_ref_hash": assessment.author_ref_hash,
                "status": assessment.status.value,
                "quality_score": assessment.quality_score,
                "safety_risk_score": assessment.safety_risk_score,
                "flags": list(assessment.flags),
                "policy_decision": policy_decision.value,
                "policy_revision": thresholds.revision,
                "policy_reasons": list(assessment.policy_reasons),
                "evidence_hash": assessment.evidence_hash,
                "context_keys": ",".join(sorted(request.context)),
                **decision_explanation_audit_metadata(explanation),
            },
            timestamp=assessment.assessed_at,
            correlation_id=correlation_id,
            actor_hash=subject_ref_hash(tenant_id=tenant_id, subject_id=created_by),
            source=NEURO_AGENT_SOURCE,
        )
        explanation = explanation.model_copy(
            update={"audit_hash": audit_record.audit_hash}
        )
        agent_run = AgentRun(
            run_id=run_id,
            tenant_id=tenant_id,
            task_type=AgentTaskType.CONTENT_HYGIENE,
            status=run_status,
            policy_decision=policy_decision,
            policy_revision=thresholds.revision,
            policy_reasons=assessment.policy_reasons,
            content_hygiene=assessment,
            decision_explanation=explanation,
            audit_hash=audit_record.audit_hash,
            created_by=created_by,
            created_at=assessment.assessed_at,
            updated_at=assessment.assessed_at,
        )
        await self.publisher.publish(
            EventEnvelope(
                event_id=event_id or _new_id("evt-content-hygiene"),
                type=event_type,
                schema_version=NEURO_AGENT_SCHEMA_VERSION,
                tenant_id=tenant_id,
                source=NEURO_AGENT_SOURCE,
                correlation_id=correlation_id,
                occurred_at=assessment.assessed_at,
                payload={
                    "run_id": agent_run.run_id,
                    "content_id": request.content_id,
                    "content_hash": assessment.content_hash,
                    "status": assessment.status.value,
                    "quality_score": assessment.quality_score,
                    "safety_risk_score": assessment.safety_risk_score,
                    "flags": list(assessment.flags),
                    "policy_decision": policy_decision.value,
                    "policy_revision": thresholds.revision,
                    "policy_reasons": list(assessment.policy_reasons),
                    "audit_hash": agent_run.audit_hash,
                    **decision_explanation_event_payload(explanation),
                },
            )
        )
        return agent_run

    async def _run_publication_optimization(
        self,
        *,
        tenant_id: str,
        created_by: str,
        correlation_id: str,
        run_id: str,
        event_id: str | None,
        request: PublicationOptimizationRequest,
        thresholds: CouncilThresholds,
        created_at: datetime,
    ) -> AgentRun:
        reported_at = normalize_datetime(request.created_at or created_at)
        report = build_publication_analytics_report(
            tenant_id=tenant_id,
            request=request,
            thresholds=thresholds,
            created_at=reported_at,
        )
        reasons = recommendation_policy_reasons(report.recommendations)
        policy_decision = PolicyDecision.ESCALATE if reasons else PolicyDecision.ALLOW
        run_status = (
            AgentRunStatus.NEEDS_COUNCIL_REVIEW
            if policy_decision is PolicyDecision.ESCALATE
            else AgentRunStatus.COMPLETED
        )
        explanation = build_decision_explanation(
            tenant_id=tenant_id,
            run_id=run_id,
            task_type=AgentTaskType.PUBLICATION_OPTIMIZATION,
            status=run_status,
            policy_decision=policy_decision,
            policy_revision=thresholds.revision,
            reason_codes=reasons,
            input_facts={
                "publication_id": request.publication_id,
                "platform": request.platform,
                "engagement_rate": report.engagement_rate,
                "click_through_rate": report.click_through_rate,
                "conversion_rate": report.conversion_rate,
                "performance_band": report.performance_band,
                "recommendation_count": len(report.recommendations),
                "recommendation_actions": [
                    recommendation.action for recommendation in report.recommendations
                ],
            },
            evidence_refs=(f"publication_evidence:{report.evidence_hash}",),
            action_refs=(f"publication_optimization:{request.publication_id}",),
            created_at=report.created_at,
        )
        audit_record = self.audit_logger.record(
            event_type=PUBLICATION_ANALYTICS_CREATED_EVENT,
            tenant_id=tenant_id,
            metadata={
                "run_id": run_id,
                "publication_id": request.publication_id,
                "platform": request.platform,
                "impressions": report.impressions,
                "reach": report.reach,
                "engagement_rate": report.engagement_rate,
                "click_through_rate": report.click_through_rate,
                "conversion_rate": report.conversion_rate,
                "performance_band": report.performance_band,
                "recommendation_count": len(report.recommendations),
                "recommendation_actions": [
                    recommendation.action for recommendation in report.recommendations
                ],
                "policy_decision": policy_decision.value,
                "policy_revision": thresholds.revision,
                "policy_reasons": list(reasons),
                "evidence_hash": report.evidence_hash,
                **decision_explanation_audit_metadata(explanation),
            },
            timestamp=report.created_at,
            correlation_id=correlation_id,
            actor_hash=subject_ref_hash(tenant_id=tenant_id, subject_id=created_by),
            source=NEURO_AGENT_SOURCE,
        )
        explanation = explanation.model_copy(
            update={"audit_hash": audit_record.audit_hash}
        )
        agent_run = AgentRun(
            run_id=run_id,
            tenant_id=tenant_id,
            task_type=AgentTaskType.PUBLICATION_OPTIMIZATION,
            status=run_status,
            policy_decision=policy_decision,
            policy_revision=thresholds.revision,
            policy_reasons=reasons,
            publication_analytics=report,
            decision_explanation=explanation,
            audit_hash=audit_record.audit_hash,
            created_by=created_by,
            created_at=report.created_at,
            updated_at=report.created_at,
        )
        await self.publisher.publish(
            EventEnvelope(
                event_id=event_id or _new_id("evt-publication-analytics"),
                type=PUBLICATION_ANALYTICS_CREATED_EVENT,
                schema_version=NEURO_AGENT_SCHEMA_VERSION,
                tenant_id=tenant_id,
                source=NEURO_AGENT_SOURCE,
                correlation_id=correlation_id,
                occurred_at=report.created_at,
                payload={
                    "run_id": agent_run.run_id,
                    "publication_id": request.publication_id,
                    "performance_band": report.performance_band,
                    "recommendation_count": len(report.recommendations),
                    "policy_decision": policy_decision.value,
                    "policy_revision": thresholds.revision,
                    "policy_reasons": list(reasons),
                    "audit_hash": agent_run.audit_hash,
                    **decision_explanation_event_payload(explanation),
                },
            )
        )
        return agent_run

    async def _run_agentic_rag(
        self,
        *,
        tenant_id: str,
        created_by: str,
        correlation_id: str,
        run_id: str,
        event_id: str | None,
        request: AgenticRagQueryRequest,
        thresholds: CouncilThresholds,
        created_at: datetime,
    ) -> AgentRun:
        answered_at = normalize_datetime(request.created_at or created_at)
        answer = await self._build_agentic_rag_answer(
            tenant_id=tenant_id,
            subject=created_by,
            correlation_id=correlation_id,
            request=request,
            created_at=answered_at,
        )
        explanation = build_decision_explanation(
            tenant_id=tenant_id,
            run_id=run_id,
            task_type=AgentTaskType.AGENTIC_RAG,
            status=AgentRunStatus.COMPLETED,
            policy_decision=PolicyDecision.ALLOW,
            policy_revision=thresholds.revision,
            reason_codes=(),
            input_facts={
                "query_id": request.query_id,
                "query_hash": answer.query_hash,
                "retrieval_count": answer.retrieval_count,
                "document_ids": [item.document_id for item in answer.context_items],
            },
            evidence_refs=(
                f"rag_evidence:{answer.evidence_hash}",
                *(f"content_hash:{item.content_hash}" for item in answer.context_items),
            ),
            action_refs=(f"agentic_rag:{request.query_id}",),
            created_at=answered_at,
        )
        audit_record = self.audit_logger.record(
            event_type=RAG_QUERY_COMPLETED_EVENT,
            tenant_id=tenant_id,
            metadata={
                "run_id": run_id,
                "query_id": request.query_id,
                "query_hash": answer.query_hash,
                "retrieval_count": answer.retrieval_count,
                "document_ids": [item.document_id for item in answer.context_items],
                "content_hashes": [item.content_hash for item in answer.context_items],
                "policy_revision": thresholds.revision,
                "evidence_hash": answer.evidence_hash,
                **decision_explanation_audit_metadata(explanation),
            },
            timestamp=answered_at,
            correlation_id=correlation_id,
            actor_hash=subject_ref_hash(tenant_id=tenant_id, subject_id=created_by),
            source=NEURO_AGENT_SOURCE,
        )
        explanation = explanation.model_copy(
            update={"audit_hash": audit_record.audit_hash}
        )
        agent_run = AgentRun(
            run_id=run_id,
            tenant_id=tenant_id,
            task_type=AgentTaskType.AGENTIC_RAG,
            status=AgentRunStatus.COMPLETED,
            policy_decision=PolicyDecision.ALLOW,
            policy_revision=thresholds.revision,
            rag_answer=answer,
            decision_explanation=explanation,
            audit_hash=audit_record.audit_hash,
            created_by=created_by,
            created_at=answered_at,
            updated_at=answered_at,
        )
        await self.publisher.publish(
            EventEnvelope(
                event_id=event_id or _new_id("evt-rag-query-completed"),
                type=RAG_QUERY_COMPLETED_EVENT,
                schema_version=NEURO_AGENT_SCHEMA_VERSION,
                tenant_id=tenant_id,
                source=NEURO_AGENT_SOURCE,
                correlation_id=correlation_id,
                occurred_at=answered_at,
                payload={
                    "run_id": agent_run.run_id,
                    "query_id": request.query_id,
                    "query_hash": answer.query_hash,
                    "retrieval_count": answer.retrieval_count,
                    "document_ids": [item.document_id for item in answer.context_items],
                    "audit_hash": agent_run.audit_hash,
                    **decision_explanation_event_payload(explanation),
                },
            )
        )
        return agent_run

    async def _run_deep_research(
        self,
        *,
        tenant_id: str,
        created_by: str,
        correlation_id: str,
        run_id: str,
        event_id: str | None,
        request: DeepResearchRequest,
        thresholds: CouncilThresholds,
        created_at: datetime,
    ) -> AgentRun:
        drafted_at = normalize_datetime(request.created_at or created_at)
        draft = await self._build_deep_research_draft(
            tenant_id=tenant_id,
            subject=created_by,
            correlation_id=correlation_id,
            request=request,
            created_at=drafted_at,
        )
        explanation = build_decision_explanation(
            tenant_id=tenant_id,
            run_id=run_id,
            task_type=AgentTaskType.DEEP_RESEARCH,
            status=AgentRunStatus.COMPLETED,
            policy_decision=PolicyDecision.ALLOW,
            policy_revision=thresholds.revision,
            reason_codes=(),
            input_facts={
                "research_id": request.research_id,
                "question_hash": draft.question_hash,
                "citation_count": draft.citation_count,
                "requires_human_review": draft.requires_human_review,
                "document_ids": [citation.document_id for citation in draft.citations],
            },
            evidence_refs=(
                f"research_evidence:{draft.evidence_hash}",
                *(
                    f"content_hash:{citation.content_hash}"
                    for citation in draft.citations
                ),
            ),
            action_refs=(f"deep_research:{request.research_id}",),
            created_at=drafted_at,
        )
        audit_record = self.audit_logger.record(
            event_type=DEEP_RESEARCH_DRAFT_CREATED_EVENT,
            tenant_id=tenant_id,
            metadata={
                "run_id": run_id,
                "research_id": request.research_id,
                "question_hash": draft.question_hash,
                "citation_count": draft.citation_count,
                "document_ids": [citation.document_id for citation in draft.citations],
                "requires_human_review": draft.requires_human_review,
                "policy_revision": thresholds.revision,
                "evidence_hash": draft.evidence_hash,
                **decision_explanation_audit_metadata(explanation),
            },
            timestamp=drafted_at,
            correlation_id=correlation_id,
            actor_hash=subject_ref_hash(tenant_id=tenant_id, subject_id=created_by),
            source=NEURO_AGENT_SOURCE,
        )
        explanation = explanation.model_copy(
            update={"audit_hash": audit_record.audit_hash}
        )
        agent_run = AgentRun(
            run_id=run_id,
            tenant_id=tenant_id,
            task_type=AgentTaskType.DEEP_RESEARCH,
            status=AgentRunStatus.COMPLETED,
            policy_decision=PolicyDecision.ALLOW,
            policy_revision=thresholds.revision,
            deep_research_draft=draft,
            decision_explanation=explanation,
            audit_hash=audit_record.audit_hash,
            created_by=created_by,
            created_at=drafted_at,
            updated_at=drafted_at,
        )
        await self.publisher.publish(
            EventEnvelope(
                event_id=event_id or _new_id("evt-deep-research-draft-created"),
                type=DEEP_RESEARCH_DRAFT_CREATED_EVENT,
                schema_version=NEURO_AGENT_SCHEMA_VERSION,
                tenant_id=tenant_id,
                source=NEURO_AGENT_SOURCE,
                correlation_id=correlation_id,
                occurred_at=drafted_at,
                payload={
                    "run_id": agent_run.run_id,
                    "research_id": request.research_id,
                    "question_hash": draft.question_hash,
                    "citation_count": draft.citation_count,
                    "requires_human_review": draft.requires_human_review,
                    "audit_hash": agent_run.audit_hash,
                    **decision_explanation_event_payload(explanation),
                },
            )
        )
        return agent_run

    async def _run_content_agent_action(
        self,
        *,
        tenant_id: str,
        created_by: str,
        correlation_id: str,
        run_id: str,
        event_id: str | None,
        request: ContentAgentActionRequest,
        thresholds: CouncilThresholds,
        created_at: datetime,
    ) -> AgentRun:
        proposed_at = normalize_datetime(request.created_at or created_at)
        reasons = content_agent_policy_reasons(
            request=request,
            thresholds=thresholds,
        )
        action_plan = build_content_agent_action_plan(
            tenant_id=tenant_id,
            request=request,
            policy_reasons=reasons,
            proposed_at=proposed_at,
        )
        explanation = build_decision_explanation(
            tenant_id=tenant_id,
            run_id=run_id,
            task_type=AgentTaskType.CONTENT_AGENT_ACTION,
            status=AgentRunStatus.NEEDS_COUNCIL_REVIEW,
            policy_decision=PolicyDecision.ESCALATE,
            policy_revision=thresholds.revision,
            reason_codes=reasons,
            input_facts={
                "action_id": request.action_id,
                "risk_score": request.risk_score,
                "agent_confidence": request.agent_confidence,
                "action_count": len(action_plan.actions),
                "action_types": [action.action_type for action in action_plan.actions],
                "requires_human_approval": action_plan.requires_human_approval,
                "auto_executed": action_plan.auto_executed,
            },
            evidence_refs=(
                f"workspace_ref_hash:{action_plan.workspace_ref_hash}",
                f"action_evidence:{action_plan.evidence_hash}",
                *(
                    f"target_ref_hash:{action.target_ref_hash}"
                    for action in action_plan.actions
                ),
            ),
            action_refs=(f"content_agent_action:{request.action_id}",),
            created_at=proposed_at,
        )
        audit_record = self.audit_logger.record(
            event_type=CONTENT_AGENT_ACTION_PROPOSED_EVENT,
            tenant_id=tenant_id,
            metadata={
                "run_id": run_id,
                "action_id": request.action_id,
                "workspace_ref_hash": action_plan.workspace_ref_hash,
                "action_types": [action.action_type for action in action_plan.actions],
                "target_ref_hashes": [
                    action.target_ref_hash for action in action_plan.actions
                ],
                "approval_status": action_plan.approval_status.value,
                "requires_human_approval": action_plan.requires_human_approval,
                "auto_executed": action_plan.auto_executed,
                "policy_decision": PolicyDecision.ESCALATE.value,
                "policy_revision": thresholds.revision,
                "policy_reasons": list(reasons),
                "evidence_hash": action_plan.evidence_hash,
                **decision_explanation_audit_metadata(explanation),
            },
            timestamp=proposed_at,
            correlation_id=correlation_id,
            actor_hash=subject_ref_hash(tenant_id=tenant_id, subject_id=created_by),
            source=NEURO_AGENT_SOURCE,
        )
        explanation = explanation.model_copy(
            update={"audit_hash": audit_record.audit_hash}
        )
        agent_run = AgentRun(
            run_id=run_id,
            tenant_id=tenant_id,
            task_type=AgentTaskType.CONTENT_AGENT_ACTION,
            status=AgentRunStatus.NEEDS_COUNCIL_REVIEW,
            policy_decision=PolicyDecision.ESCALATE,
            policy_revision=thresholds.revision,
            policy_reasons=reasons,
            content_agent_action=action_plan,
            decision_explanation=explanation,
            audit_hash=audit_record.audit_hash,
            created_by=created_by,
            created_at=proposed_at,
            updated_at=proposed_at,
        )
        await self.publisher.publish(
            EventEnvelope(
                event_id=event_id or _new_id("evt-content-agent-action-proposed"),
                type=CONTENT_AGENT_ACTION_PROPOSED_EVENT,
                schema_version=NEURO_AGENT_SCHEMA_VERSION,
                tenant_id=tenant_id,
                source=NEURO_AGENT_SOURCE,
                correlation_id=correlation_id,
                occurred_at=proposed_at,
                payload={
                    "run_id": agent_run.run_id,
                    "action_id": request.action_id,
                    "workspace_ref_hash": action_plan.workspace_ref_hash,
                    "approval_status": action_plan.approval_status.value,
                    "requires_human_approval": action_plan.requires_human_approval,
                    "auto_executed": action_plan.auto_executed,
                    "policy_reasons": list(reasons),
                    "audit_hash": agent_run.audit_hash,
                    **decision_explanation_event_payload(explanation),
                },
            )
        )
        return agent_run

    async def _build_agentic_rag_answer(
        self,
        *,
        tenant_id: str,
        subject: str,
        correlation_id: str,
        request: AgenticRagQueryRequest,
        created_at: datetime,
    ) -> AgenticRagAnswer:
        context_items = await self._retrieve_rag_context(
            tenant_id=tenant_id,
            subject=subject,
            correlation_id=correlation_id,
            query_text=request.query_text,
            limit=request.limit,
            source_type=request.source_type,
        )
        query_hash = scoped_ref_hash(
            tenant_id=tenant_id,
            namespace="rag_query",
            value=request.query_text,
        )
        evidence_hash = _hash_json(
            {
                "tenant_id": tenant_id,
                "query_id": request.query_id,
                "query_hash": query_hash,
                "document_ids": [item.document_id for item in context_items],
                "content_hashes": [item.content_hash for item in context_items],
            }
        )
        return AgenticRagAnswer(
            query_id=request.query_id,
            tenant_id=tenant_id,
            query_hash=query_hash,
            answer_text=render_rag_answer(
                query_text=request.query_text,
                context_items=context_items,
            ),
            context_items=context_items,
            retrieval_count=len(context_items),
            evidence_hash=evidence_hash,
            created_at=created_at,
        )

    async def _build_deep_research_draft(
        self,
        *,
        tenant_id: str,
        subject: str,
        correlation_id: str,
        request: DeepResearchRequest,
        created_at: datetime,
    ) -> DeepResearchDraft:
        query_text = request.query_text or request.question
        context_items = await self._retrieve_rag_context(
            tenant_id=tenant_id,
            subject=subject,
            correlation_id=correlation_id,
            query_text=query_text,
            limit=request.limit,
            source_type=None,
        )
        citations = tuple(
            DeepResearchCitation(
                document_id=item.document_id,
                source_type=item.source_type,
                source_ref_hash=item.source_ref_hash,
                content_hash=item.content_hash,
                excerpt=item.excerpt,
            )
            for item in context_items
        )
        question_hash = scoped_ref_hash(
            tenant_id=tenant_id,
            namespace="deep_research_question",
            value=request.question,
        )
        evidence_hash = _hash_json(
            {
                "tenant_id": tenant_id,
                "research_id": request.research_id,
                "question_hash": question_hash,
                "citation_ids": [citation.document_id for citation in citations],
                "citation_hashes": [citation.content_hash for citation in citations],
            }
        )
        return DeepResearchDraft(
            research_id=request.research_id,
            question_hash=question_hash,
            title=deep_research_title(request.question),
            draft_text=render_deep_research_draft(
                question=request.question,
                citations=citations,
            ),
            outline=deep_research_outline(citations),
            citations=citations,
            citation_count=len(citations),
            draft_status=DeepResearchDraftStatus.DRAFTED,
            requires_human_review=True,
            evidence_hash=evidence_hash,
            created_at=created_at,
        )

    async def _retrieve_rag_context(
        self,
        *,
        tenant_id: str,
        subject: str,
        correlation_id: str,
        query_text: str,
        limit: int,
        source_type: str | None,
    ) -> tuple[AgenticRagContextItem, ...]:
        context = _tenant_context(
            tenant_id=tenant_id,
            subject=subject,
            correlation_id=correlation_id,
        )
        metadata_filter = {"source_type": source_type} if source_type else None
        results = await self.vector_store.query(
            RAG_VECTOR_DOMAIN,
            text_embedding(query_text),
            context=context,
            limit=limit,
            metadata_filter=metadata_filter,
        )
        return tuple(rag_context_item_from_vector_result(result) for result in results)


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


def assess_content_hygiene(
    *,
    tenant_id: str,
    request: ContentHygieneRequest,
    thresholds: CouncilThresholds,
    assessed_at: datetime,
) -> ContentHygieneAssessment:
    flags = content_hygiene_flags(request.content_text)
    quality_score = content_quality_score(request.content_text)
    safety_risk_score = content_safety_risk_score(flags)
    reasons = content_hygiene_policy_reasons(
        quality_score=quality_score,
        safety_risk_score=safety_risk_score,
        thresholds=thresholds,
    )
    content_hash = scoped_ref_hash(
        tenant_id=tenant_id,
        namespace="content",
        value=request.content_text,
    )
    author_ref_hash = (
        scoped_ref_hash(
            tenant_id=tenant_id,
            namespace="author",
            value=request.author_ref,
        )
        if request.author_ref is not None
        else None
    )
    evidence_hash = _hash_json(
        {
            "tenant_id": tenant_id,
            "content_id": request.content_id,
            "platform": request.platform,
            "content_hash": content_hash,
            "author_ref_hash": author_ref_hash,
            "quality_score": quality_score,
            "safety_risk_score": safety_risk_score,
            "flags": list(flags),
            "policy_reasons": list(reasons),
        }
    )
    status = ContentHygieneStatus.FLAGGED if reasons else ContentHygieneStatus.PASSED
    return ContentHygieneAssessment(
        content_id=request.content_id,
        platform=request.platform,
        content_hash=content_hash,
        author_ref_hash=author_ref_hash,
        status=status,
        quality_score=quality_score,
        safety_risk_score=safety_risk_score,
        flags=flags,
        policy_reasons=reasons,
        evidence_hash=evidence_hash,
        assessed_at=assessed_at,
    )


def content_hygiene_flags(content_text: str) -> tuple[str, ...]:
    normalized_text = content_text.strip()
    lowered_text = normalized_text.lower()
    words = _word_tokens(normalized_text)
    flags: list[str] = []

    if any(keyword in lowered_text for keyword in _UNSAFE_CONTENT_KEYWORDS):
        flags.append("unsafe_keyword")
    if len(words) < 8:
        flags.append("too_short")
    if _REPEATED_PUNCTUATION_RE.search(normalized_text):
        flags.append("excessive_punctuation")
    if uppercase_ratio(normalized_text) > 0.45:
        flags.append("excessive_caps")
    if len(words) >= 8 and unique_word_ratio(words) < 0.45:
        flags.append("repetitive_text")

    return tuple(dict.fromkeys(flags))


def content_quality_score(content_text: str) -> float:
    normalized_text = content_text.strip()
    words = _word_tokens(normalized_text)
    score = 1.0

    if len(words) < 20:
        score -= 0.3
    if len(normalized_text) < 160:
        score -= 0.2
    if _REPEATED_PUNCTUATION_RE.search(normalized_text):
        score -= 0.1
    if uppercase_ratio(normalized_text) > 0.45:
        score -= 0.1
    if len(words) >= 8 and unique_word_ratio(words) < 0.45:
        score -= 0.15

    return round(min(1.0, max(0.0, score)), 4)


def content_safety_risk_score(flags: tuple[str, ...]) -> float:
    score = 0.05
    if "unsafe_keyword" in flags:
        score += 0.5
    if "repetitive_text" in flags:
        score += 0.2
    if "excessive_caps" in flags:
        score += 0.1
    if "excessive_punctuation" in flags:
        score += 0.1

    return round(min(1.0, score), 4)


def content_hygiene_policy_reasons(
    *,
    quality_score: float,
    safety_risk_score: float,
    thresholds: CouncilThresholds,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if safety_risk_score > thresholds.max_autonomous_risk_score:
        reasons.append("content_safety_risk_above_threshold")
    if quality_score < thresholds.min_content_quality_score:
        reasons.append("content_quality_below_threshold")

    return tuple(reasons)


def build_publication_analytics_report(
    *,
    tenant_id: str,
    request: PublicationOptimizationRequest,
    thresholds: CouncilThresholds,
    created_at: datetime,
) -> PublicationAnalyticsReport:
    metrics = request.metrics
    engagement_rate = safe_rate(
        metrics.engagement_count(),
        metrics.engagement_base(),
    )
    click_through_rate = safe_rate(metrics.clicks, metrics.impressions)
    conversion_rate = safe_rate(metrics.conversions, metrics.clicks)
    share_rate = safe_rate(metrics.shares, metrics.engagement_base())
    recommendations = build_optimization_recommendations(
        publication_id=request.publication_id,
        engagement_rate=engagement_rate,
        click_through_rate=click_through_rate,
        share_rate=share_rate,
        agent_confidence=request.agent_confidence,
        recommendation_risk_score=request.recommendation_risk_score,
        thresholds=thresholds,
    )
    evidence_hash = _hash_json(
        {
            "tenant_id": tenant_id,
            "publication_id": request.publication_id,
            "platform": request.platform,
            "published_at": format_datetime(request.published_at),
            "metrics": metrics.model_dump(mode="json"),
            "topic_tags": list(request.topic_tags),
            "recommendations": [
                recommendation.model_dump(mode="json")
                for recommendation in recommendations
            ],
        }
    )
    return PublicationAnalyticsReport(
        publication_id=request.publication_id,
        platform=request.platform,
        published_at=request.published_at,
        impressions=metrics.impressions,
        reach=metrics.reach,
        engagement_rate=engagement_rate,
        click_through_rate=click_through_rate,
        conversion_rate=conversion_rate,
        share_rate=share_rate,
        performance_band=publication_performance_band(
            engagement_rate=engagement_rate,
            click_through_rate=click_through_rate,
        ),
        topic_tags=request.topic_tags,
        recommendations=recommendations,
        evidence_hash=evidence_hash,
        created_at=created_at,
    )


def build_rag_document_reference(
    *,
    tenant_id: str,
    document: RagDocumentInput,
    created_at: datetime,
) -> RagDocumentReference:
    return RagDocumentReference(
        document_id=document.document_id,
        source_type=document.source_type,
        source_ref_hash=(
            scoped_ref_hash(
                tenant_id=tenant_id,
                namespace="rag_source",
                value=document.source_ref,
            )
            if document.source_ref is not None
            else None
        ),
        content_hash=scoped_ref_hash(
            tenant_id=tenant_id,
            namespace="rag_content",
            value=document.content,
        ),
        topic_tags=document.topic_tags,
        created_at=created_at,
    )


def rag_document_vector_record(
    *,
    document: RagDocumentInput,
    reference: RagDocumentReference,
) -> VectorRecord:
    metadata: dict[str, str] = {
        "document_id": reference.document_id,
        "source_type": reference.source_type,
        "content_hash": reference.content_hash,
        "topic_tags": ",".join(reference.topic_tags),
        "created_at": format_datetime(reference.created_at),
    }
    if reference.source_ref_hash is not None:
        metadata["source_ref_hash"] = reference.source_ref_hash

    return VectorRecord(
        id=document.document_id,
        embedding=text_embedding(document.content),
        document=document.content,
        metadata=metadata,
    )


def rag_context_item_from_vector_result(
    result: VectorSearchResult,
) -> AgenticRagContextItem:
    content_hash = _metadata_string(result.metadata, "content_hash")
    if content_hash == "":
        content_hash = scoped_ref_hash(
            tenant_id=_metadata_string(result.metadata, "tenant_id"),
            namespace="rag_content",
            value=result.document or result.id,
        )

    return AgenticRagContextItem(
        document_id=_metadata_string(result.metadata, "document_id", result.id),
        source_type=_metadata_string(result.metadata, "source_type", "unknown"),
        source_ref_hash=_optional_metadata_string(result.metadata, "source_ref_hash"),
        content_hash=content_hash,
        excerpt=content_excerpt(result.document or ""),
        distance=result.distance,
        relevance_score=relevance_score(result.distance),
        topic_tags=_metadata_topic_tags(result.metadata),
    )


def render_rag_answer(
    *,
    query_text: str,
    context_items: tuple[AgenticRagContextItem, ...],
) -> str:
    if not context_items:
        return (
            "Tenant-scoped RAG не нашел релевантный контекст. "
            "Перед публикацией нужен ручной подбор источников."
        )

    snippets = " ".join(item.excerpt for item in context_items[:3])
    return (
        f"Найден tenant-scoped контекст для запроса '{query_text.strip()}': {snippets}"
    )


def deep_research_title(question: str) -> str:
    normalized = content_excerpt(question, max_length=96)
    return f"Черновик исследования: {normalized}"


def render_deep_research_draft(
    *,
    question: str,
    citations: tuple[DeepResearchCitation, ...],
) -> str:
    if not citations:
        return (
            "Черновик: источники в tenant-scoped RAG пока не найдены. "
            f"Вопрос исследования: {question.strip()}"
        )

    evidence = " ".join(citation.excerpt for citation in citations[:3])
    return (
        "Черновик: материал отвечает на исследовательский вопрос и должен пройти "
        "человеческую редактуру перед публикацией. "
        f"Вопрос: {question.strip()} Контекст источников: {evidence}"
    )


def deep_research_outline(
    citations: tuple[DeepResearchCitation, ...],
) -> tuple[str, ...]:
    if not citations:
        return (
            "Уточнить источники",
            "Сформировать тезисы",
            "Передать человеку на проверку",
        )

    return (
        "Контекст и источники",
        "Ключевые тезисы черновика",
        "Проверка человеком перед публикацией",
    )


def content_agent_policy_reasons(
    *,
    request: ContentAgentActionRequest,
    thresholds: CouncilThresholds,
) -> tuple[str, ...]:
    reasons = ["human_approval_required"]
    if request.risk_score > thresholds.max_autonomous_risk_score:
        reasons.append("risk_score_above_threshold")
    if request.agent_confidence < thresholds.min_agent_confidence:
        reasons.append("confidence_below_threshold")

    return tuple(reasons)


def build_content_agent_action_plan(
    *,
    tenant_id: str,
    request: ContentAgentActionRequest,
    policy_reasons: tuple[str, ...],
    proposed_at: datetime,
) -> ContentAgentActionPlan:
    workspace_ref_hash = scoped_ref_hash(
        tenant_id=tenant_id,
        namespace="content_agent_workspace",
        value=request.workspace_ref,
    )
    actions = tuple(
        ContentAgentActionStep(
            action_type=action.action_type,
            target_ref_hash=scoped_ref_hash(
                tenant_id=tenant_id,
                namespace="content_agent_target",
                value=action.target_ref,
            ),
            summary=action.summary,
        )
        for action in request.proposed_actions
    )
    evidence_hash = _hash_json(
        {
            "tenant_id": tenant_id,
            "action_id": request.action_id,
            "workspace_ref_hash": workspace_ref_hash,
            "goal_hash": scoped_ref_hash(
                tenant_id=tenant_id,
                namespace="content_agent_goal",
                value=request.goal,
            ),
            "target_ref_hashes": [action.target_ref_hash for action in actions],
            "risk_score": request.risk_score,
            "agent_confidence": request.agent_confidence,
            "policy_reasons": list(policy_reasons),
        }
    )

    return ContentAgentActionPlan(
        action_id=request.action_id,
        workspace_ref_hash=workspace_ref_hash,
        goal=request.goal,
        actions=actions,
        risk_score=request.risk_score,
        agent_confidence=request.agent_confidence,
        approval_status=ContentAgentApprovalStatus.AWAITING_HUMAN_APPROVAL,
        requires_human_approval=True,
        auto_executed=False,
        policy_reasons=policy_reasons,
        evidence_hash=evidence_hash,
        proposed_at=proposed_at,
    )


def build_decision_explanation(
    *,
    tenant_id: str,
    run_id: str,
    task_type: AgentTaskType,
    status: AgentRunStatus,
    policy_decision: PolicyDecision,
    policy_revision: int,
    reason_codes: tuple[str, ...],
    input_facts: Mapping[str, object],
    evidence_refs: tuple[str, ...],
    action_refs: tuple[str, ...],
    created_at: datetime,
) -> DecisionExplanation:
    normalized_reasons = tuple(
        dict.fromkeys(reason.strip() for reason in reason_codes if reason.strip())
    )
    normalized_evidence_refs = tuple(
        dict.fromkeys(item.strip() for item in evidence_refs if item.strip())
    )
    normalized_action_refs = tuple(
        dict.fromkeys(item.strip() for item in action_refs if item.strip())
    )
    facts = _clone_json_mapping(input_facts)
    summary = render_decision_explanation_summary(
        task_type=task_type,
        status=status,
        policy_decision=policy_decision,
        policy_revision=policy_revision,
        reason_codes=normalized_reasons,
    )
    hash_payload = {
        "tenant_id": tenant_id,
        "run_id": run_id,
        "task_type": task_type.value,
        "status": status.value,
        "policy_decision": policy_decision.value,
        "policy_revision": policy_revision,
        "summary": summary,
        "reason_codes": list(normalized_reasons),
        "input_facts": facts,
        "evidence_refs": list(normalized_evidence_refs),
        "action_refs": list(normalized_action_refs),
        "created_at": format_datetime(created_at),
    }
    explanation_hash = _hash_json(hash_payload)
    explanation_id_hash = _hash_json({"tenant_id": tenant_id, "run_id": run_id})
    explanation_id = f"xai-{explanation_id_hash[:32]}"
    return DecisionExplanation(
        explanation_id=explanation_id,
        tenant_id=tenant_id,
        run_id=run_id,
        task_type=task_type,
        status=status,
        policy_decision=policy_decision,
        policy_revision=policy_revision,
        summary=summary,
        reason_codes=normalized_reasons,
        input_facts=facts,
        evidence_refs=normalized_evidence_refs,
        action_refs=normalized_action_refs,
        explanation_hash=explanation_hash,
        created_at=created_at,
    )


def render_decision_explanation_summary(
    *,
    task_type: AgentTaskType,
    status: AgentRunStatus,
    policy_decision: PolicyDecision,
    policy_revision: int,
    reason_codes: tuple[str, ...],
) -> str:
    if policy_decision is PolicyDecision.ESCALATE:
        reasons = ", ".join(reason_codes) if reason_codes else "manual_review"
        return (
            f"AI-решение по {task_type.value} передано Совету: {reasons}; "
            f"статус {status.value}, ревизия политики {policy_revision}."
        )

    return (
        f"AI-решение по {task_type.value} разрешено в рамках порогов Совета; "
        f"статус {status.value}, ревизия политики {policy_revision}."
    )


def decision_explanation_audit_metadata(
    explanation: DecisionExplanation,
) -> dict[str, JSONValue]:
    return cast(
        dict[str, JSONValue],
        {
            "decision_explanation_scope": DECISION_EXPLANATION_AUDIT_SCOPE,
            "decision_explanation_id": explanation.explanation_id,
            "decision_explanation_hash": explanation.explanation_hash,
            "decision_explanation_summary_hash": _hash_json(
                {"summary": explanation.summary}
            ),
            "decision_explanation_action_refs": list(explanation.action_refs),
        },
    )


def decision_explanation_event_payload(
    explanation: DecisionExplanation,
) -> dict[str, JSONValue]:
    return cast(
        dict[str, JSONValue],
        {
            "decision_explanation_id": explanation.explanation_id,
            "decision_explanation_hash": explanation.explanation_hash,
        },
    )


def text_embedding(value: str) -> tuple[float, ...]:
    vector = [0.0] * RAG_EMBEDDING_DIMENSIONS
    for token in _word_tokens(value):
        bucket = int(hashlib.sha256(token.encode("utf-8")).hexdigest()[:8], 16)
        vector[bucket % RAG_EMBEDDING_DIMENSIONS] += 1.0

    norm = sum(item * item for item in vector) ** 0.5
    if norm == 0:
        return tuple(vector)

    return tuple(round(item / norm, 6) for item in vector)


def content_excerpt(value: str, *, max_length: int = 220) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= max_length:
        return normalized

    return normalized[: max_length - 3].rstrip() + "..."


def relevance_score(distance: float) -> float:
    return round(1 / (1 + distance), 4)


def build_optimization_recommendations(
    *,
    publication_id: str,
    engagement_rate: float,
    click_through_rate: float,
    share_rate: float,
    agent_confidence: float,
    recommendation_risk_score: float,
    thresholds: CouncilThresholds,
) -> tuple[OptimizationRecommendation, ...]:
    candidates: list[tuple[str, str, str]] = []
    if engagement_rate < 0.03:
        candidates.append(
            (
                "rewrite_opening_hook",
                "engagement_below_target",
                "engagement_rate",
            )
        )
    if click_through_rate < 0.01:
        candidates.append(
            (
                "strengthen_call_to_action",
                "ctr_below_target",
                "click_through_rate",
            )
        )
    if share_rate < 0.01:
        candidates.append(
            (
                "test_publication_window",
                "share_rate_below_target",
                "share_rate",
            )
        )

    policy_reasons = optimization_policy_reasons(
        agent_confidence=agent_confidence,
        recommendation_risk_score=recommendation_risk_score,
        thresholds=thresholds,
    )
    status = (
        OptimizationRecommendationStatus.NEEDS_COUNCIL_REVIEW
        if policy_reasons
        else OptimizationRecommendationStatus.PROPOSED
    )
    return tuple(
        OptimizationRecommendation(
            recommendation_id=f"{publication_id}-{action}",
            action=action,
            rationale_code=rationale_code,
            expected_metric=expected_metric,
            confidence=agent_confidence,
            risk_score=recommendation_risk_score,
            status=status,
            auto_applied=False,
            requires_human_approval=True,
            policy_reasons=policy_reasons,
        )
        for action, rationale_code, expected_metric in candidates
    )


def optimization_policy_reasons(
    *,
    agent_confidence: float,
    recommendation_risk_score: float,
    thresholds: CouncilThresholds,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if recommendation_risk_score > thresholds.max_autonomous_risk_score:
        reasons.append("risk_score_above_threshold")
    if agent_confidence < thresholds.min_agent_confidence:
        reasons.append("confidence_below_threshold")

    return tuple(reasons)


def recommendation_policy_reasons(
    recommendations: tuple[OptimizationRecommendation, ...],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            reason
            for recommendation in recommendations
            for reason in recommendation.policy_reasons
        )
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


def normalize_token_tuple(value: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for item in value:
        token = item.strip()
        if token == "":
            raise ValueError("Список не должен содержать пустые значения")
        normalized.append(token)

    return tuple(normalized)


def safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0

    return round(numerator / denominator, 4)


def publication_performance_band(
    *,
    engagement_rate: float,
    click_through_rate: float,
) -> str:
    if engagement_rate >= 0.08 or click_through_rate >= 0.03:
        return "high"
    if engagement_rate >= 0.03 or click_through_rate >= 0.01:
        return "medium"

    return "low"


def uppercase_ratio(value: str) -> float:
    letters = [character for character in value if character.isalpha()]
    if not letters:
        return 0.0

    uppercase_count = sum(1 for character in letters if character.isupper())
    return uppercase_count / len(letters)


def unique_word_ratio(words: tuple[str, ...]) -> float:
    if not words:
        return 1.0

    return len(set(words)) / len(words)


def _word_tokens(value: str) -> tuple[str, ...]:
    return tuple(match.group(0).lower() for match in _WORD_RE.finditer(value))


def scoped_ref_hash(*, tenant_id: str, namespace: str, value: str) -> str:
    payload = f"{tenant_id}:{namespace}:{value}".encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def subject_ref_hash(*, tenant_id: str, subject_id: str) -> str:
    payload = f"{tenant_id}:{subject_id}".encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


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


def _clone_json_mapping(payload: Mapping[str, object]) -> dict[str, JSONValue]:
    return cast(
        dict[str, JSONValue],
        json.loads(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)),
    )


def _coalesce[T](value: T | None, fallback: T) -> T:
    if value is None:
        return fallback

    return value


def _metadata_string(
    metadata: Mapping[str, object],
    key: str,
    fallback: str = "",
) -> str:
    value = metadata.get(key)
    if isinstance(value, str):
        return value

    return fallback


def _optional_metadata_string(
    metadata: Mapping[str, object],
    key: str,
) -> str | None:
    value = _metadata_string(metadata, key)
    if value == "":
        return None

    return value


def _metadata_topic_tags(metadata: Mapping[str, object]) -> tuple[str, ...]:
    raw_value = _metadata_string(metadata, "topic_tags")
    if raw_value == "":
        return ()

    return tuple(item for item in raw_value.split(",") if item)


def _tenant_context(
    *,
    tenant_id: str,
    subject: str,
    correlation_id: str,
) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        subject=subject,
        roles=(),
        correlation_id=correlation_id,
    )


def _run_key(tenant_id: str, run_id: str) -> tuple[str, str]:
    return tenant_id, run_id


def _new_id(prefix: str) -> IdempotencyKey:
    return f"{prefix}-{uuid4()}"
