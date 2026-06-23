from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import html
import io
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, cast
from urllib.parse import urlencode
from uuid import uuid4

from blockchain_auditor.connector import (
    AuditBatchError,
    AuditMetadataPolicyError,
    AuditRecordConflictError,
    GrpcBlockchainAuditConnector,
    InMemoryGrpcBlockchainAuditTransport,
)
from blockchain_auditor.settings import (
    build_blockchain_auditor_settings,
)
from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from hitl_payout_gateway.confirmation_manager import (
    PAYOUT_CONFIRM_OPERATION,
    PayoutConfirmation,
    PayoutConfirmationManager,
)
from hitl_payout_gateway.queue_manager import (
    InMemoryPayoutQueueRepository,
    PayoutNotExecutableError,
    PayoutNotFoundError,
    PayoutQueueError,
    PayoutQueueItem,
    PayoutQueueManager,
    PayoutStatus,
)
from hitl_payout_gateway.veto_manager import (
    VetoDecision,
    VetoManager,
    VetoWindowClosedError,
)
from policy_manager.manager import (
    InMemoryPolicyRepository,
    PolicyManager,
    PolicyManagerError,
    PolicyNotFoundError,
    PolicyRecord,
    PolicyUpdateInput,
)
from pydantic import Field
from voice_to_chain.retention import AudioRetentionError
from voice_to_chain.service import (
    VoiceToChainError,
    VoiceToChainService,
    VoiceTranscriptionCommand,
    VoiceTranscriptionReceipt,
)
from voice_to_chain.transcription import (
    InMemoryWhisperCppTranscriber,
    WhisperCppTranscriptionError,
)

from libs.shared.audit_logger import audit_hash
from libs.shared.auth import TOTPService
from libs.shared.errors import (
    VALIDATION_ERROR_CODE,
    SharedError,
    error_response_body,
)
from libs.shared.models import (
    IdempotencyKey,
    JSONValue,
    SharedBaseModel,
    SubjectId,
    TenantId,
)
from libs.shared.rbac import (
    AUDIENCE_ROLE,
    BOARD_ROLE,
    COUNCIL_ROLE,
    MEMBER_ASSOC_ROLE,
    MEMBER_FULL_ROLE,
    PRESIDIUM_ROLE,
    AccessPolicy,
    require_access,
)
from libs.shared.server import (
    BaseAppConfig,
    create_service_runtime_app,
)
from libs.shared.service_template import ServiceTemplateConfig
from libs.shared.tenant import (
    InMemoryAuditSink,
    TenantContext,
    TenantCoreError,
    TenantScopedRepository,
    require_tenant_context,
)
from web_cabinet.analytics import (
    AnalyticsCategory,
    AnalyticsCategoryAggregate,
    AnalyticsEventRecord,
    InMemoryAnalyticsRepository,
    KPIMetric,
    KPIStatus,
    KPISummary,
    build_analytics_aggregates_response,
    build_analytics_kpi_response,
)
from web_cabinet.design_system import (
    DESIGN_SYSTEM_NAME,
    DesignSystemResponse,
    design_system_css,
    design_system_response,
    render_design_system_ui_kit,
)
from web_cabinet.wallet import (
    InMemoryWalletRepository,
    WalletBalance,
    WalletBalanceResponse,
    WalletOperationRecord,
    WalletOperationResponse,
    WalletOperationType,
)

WEB_CABINET_SERVICE_NAME = "web-cabinet"

_PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
_ANALYTICS_PERIOD_PATTERN = r"^\d{4}-((0[1-9]|1[0-2])|W(0[1-9]|[1-4][0-9]|5[0-3]))$"
_REFERRAL_LEVEL_PATTERN = r"^L[1-3]$"
_PLATFORM_TARGET_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"
_PAYOUT_REASON_CODE_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
_PAYOUT_TOTP_CODE_PATTERN = r"^\d{6,8}$"
_ONBOARDING_STEP_STATUS_PATTERN = r"^(available|in_progress|completed|blocked)$"
_ONBOARDING_READINESS_STATUS_PATTERN = r"^(in_progress|ready_for_review)$"
_VOICE_CONTENT_TYPE_PATTERN = (
    r"^audio/[A-Za-z0-9.+-]{1,64}(?:;[A-Za-z0-9_.+-]+=[A-Za-z0-9_.+-]+){0,4}$"
)
_VOICE_LANGUAGE_PATTERN = r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$"
_PRIVACY_REQUEST_TYPE_PATTERN = (
    r"^(access|rectification|erasure|processing_restriction)$"
)
_PRIVACY_REQUEST_STATUS_PATTERN = r"^(registered|completed)$"
_PRIVACY_CONSENT_STATUS_PATTERN = r"^(granted|withdrawn)$"
_COMPLIANCE_CHECKLIST_VERSION = "fz152-issue-87-2026-06-20"

WEB_CABINET_READ_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    MEMBER_FULL_ROLE,
    MEMBER_ASSOC_ROLE,
    action="web_cabinet.read",
    resource_type="web_cabinet",
)
WEB_CABINET_GOVERNANCE_READ_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    action="web_cabinet.member.read",
    resource_type="web_cabinet",
)
COUNCIL_PANEL_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    action="council_panel.manage",
    resource_type="council_panel",
)
ANALYTICS_DASHBOARD_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    MEMBER_FULL_ROLE,
    MEMBER_ASSOC_ROLE,
    action="analytics_dashboard.read",
    resource_type="analytics_dashboard",
)
ONBOARDING_READ_POLICY = AccessPolicy.allow_roles(
    AUDIENCE_ROLE,
    MEMBER_ASSOC_ROLE,
    MEMBER_FULL_ROLE,
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    action="onboarding.read",
    resource_type="onboarding",
)
ONBOARDING_GOVERNANCE_READ_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    action="onboarding.member.read",
    resource_type="onboarding",
)
VOICE_ASSISTANT_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    BOARD_ROLE,
    MEMBER_FULL_ROLE,
    MEMBER_ASSOC_ROLE,
    action="voice_assistant.use",
    resource_type="voice_assistant",
)
COMPLIANCE_READ_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    action="compliance.fz152.read",
    resource_type="privacy_compliance",
)
PRIVACY_SELF_SERVICE_POLICY = AccessPolicy.allow_roles(
    AUDIENCE_ROLE,
    MEMBER_ASSOC_ROLE,
    MEMBER_FULL_ROLE,
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    action="privacy.self_service",
    resource_type="privacy_compliance",
)
PRIVACY_GOVERNANCE_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    action="privacy.member.manage",
    resource_type="privacy_compliance",
)

_RISK_LEVEL_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}
_VETO_EXPIRING_WINDOW_SECONDS = 60 * 60


class CabinetReferralLink(SharedBaseModel):
    level: str = Field(pattern=_REFERRAL_LEVEL_PATTERN)
    owner_id: SubjectId
    url: str = Field(min_length=1, max_length=2048)
    reward_share: float = Field(ge=0, le=1, allow_inf_nan=False)


class CabinetContributionSummary(SharedBaseModel):
    member_id: SubjectId
    period: str = Field(pattern=_PERIOD_PATTERN)
    total_points: float = Field(ge=0, allow_inf_nan=False)
    avg_points_council: float = Field(ge=0, allow_inf_nan=False)
    kv_raw: float = Field(ge=0, allow_inf_nan=False)
    kv_capped: float = Field(ge=0, allow_inf_nan=False)
    payout_share: float = Field(ge=0, le=1, allow_inf_nan=False)
    contribution_count: int = Field(ge=0)


class CabinetContentItem(SharedBaseModel):
    content_id: str = Field(min_length=1, max_length=128)
    template_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    preview: str = Field(min_length=1, max_length=500)
    content_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    platform_targets: tuple[
        Annotated[str, Field(pattern=_PLATFORM_TARGET_PATTERN)],
        ...,
    ] = Field(default_factory=tuple)
    points_awarded: float = Field(ge=0, allow_inf_nan=False)
    created_at: datetime


class WebCabinetOverviewResponse(SharedBaseModel):
    tenant_id: TenantId
    member_id: SubjectId
    period: str = Field(pattern=_PERIOD_PATTERN)
    contribution: CabinetContributionSummary
    balance: WalletBalanceResponse
    operations: tuple[WalletOperationResponse, ...]
    content: tuple[CabinetContentItem, ...]
    referral_links: tuple[CabinetReferralLink, ...]
    generated_at: datetime


class CouncilPanelSummary(SharedBaseModel):
    queued: int = Field(ge=0)
    ready_to_execute: int = Field(ge=0)
    canceled: int = Field(ge=0)
    executed: int = Field(ge=0)
    requires_2fa: int = Field(ge=0)
    veto_window_expiring: int = Field(ge=0)


class CouncilPanelTwoFactorStatus(SharedBaseModel):
    active: bool
    method: str


class CouncilPanelCalculation(SharedBaseModel):
    source: str = Field(min_length=1, max_length=128)
    explanation: str = Field(min_length=1, max_length=512)
    distribution_id: str = Field(min_length=1, max_length=128)
    distribution_hash: str = Field(min_length=64, max_length=64)
    payout_share: float = Field(ge=0, le=1, allow_inf_nan=False)


class CouncilPanelAuditItem(SharedBaseModel):
    event_type: str = Field(min_length=1, max_length=128)
    event_id: str = Field(min_length=1, max_length=128)
    audit_hash: str = Field(min_length=64, max_length=64)
    occurred_at: datetime


class CouncilPanelPayoutItem(SharedBaseModel):
    payout_id: str = Field(min_length=1, max_length=128)
    member_id: SubjectId
    period: str = Field(pattern=_PERIOD_PATTERN)
    payout_share: float = Field(ge=0, le=1, allow_inf_nan=False)
    status: PayoutStatus
    veto_until: datetime
    veto_seconds_left: int = Field(ge=0)
    requires_2fa: bool
    confirmation_id: str | None = None
    risk_level: str = Field(min_length=1, max_length=32)
    risk_reason: str = Field(min_length=1, max_length=512)
    policy_key: str = Field(min_length=1, max_length=128)
    policy_version: int = Field(ge=1)
    veto_available: bool
    confirm_available: bool
    calculation: CouncilPanelCalculation
    audit_timeline: tuple[CouncilPanelAuditItem, ...] = Field(default_factory=tuple)


class CouncilPanelOverviewResponse(SharedBaseModel):
    tenant_id: TenantId
    role: str
    summary: CouncilPanelSummary
    two_factor: CouncilPanelTwoFactorStatus
    payouts: tuple[CouncilPanelPayoutItem, ...]
    policies: tuple[PolicyRecord, ...]
    generated_at: datetime


class AnalyticsDashboardPeriodSlice(SharedBaseModel):
    period: str = Field(pattern=_ANALYTICS_PERIOD_PATTERN)
    event_count: int = Field(ge=0)
    unique_members: int = Field(ge=0)
    metrics_on_track: int = Field(ge=0)
    metrics_below_target: int = Field(ge=0)
    metrics_above_target: int = Field(ge=0)


class AnalyticsDashboardOverviewResponse(SharedBaseModel):
    tenant_id: TenantId
    period: str = Field(pattern=_ANALYTICS_PERIOD_PATTERN)
    category: AnalyticsCategory | None = None
    summary: KPISummary
    metrics: tuple[KPIMetric, ...]
    categories: tuple[AnalyticsCategoryAggregate, ...]
    period_slices: tuple[AnalyticsDashboardPeriodSlice, ...]
    export_url: str = Field(min_length=1, max_length=512)
    generated_at: datetime


class OnboardingStepItem(SharedBaseModel):
    step_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=512)
    order: int = Field(ge=1)
    required: bool
    status: str = Field(pattern=_ONBOARDING_STEP_STATUS_PATTERN)
    completed_at: datetime | None = None


class OnboardingConsentItem(SharedBaseModel):
    key: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=256)
    required: bool
    granted: bool
    granted_at: datetime | None = None


class OnboardingAssistantQuestionItem(SharedBaseModel):
    question_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=512)
    answer: str = Field(min_length=1, max_length=1200)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    source_refs: tuple[str, ...] = Field(default_factory=tuple)
    escalation_available: bool


class OnboardingAssistantSummary(SharedBaseModel):
    enabled: bool
    answered_questions: int = Field(ge=0)
    suggested_questions: tuple[OnboardingAssistantQuestionItem, ...]


class OnboardingReadiness(SharedBaseModel):
    required_steps_total: int = Field(ge=0)
    completed_required_steps: int = Field(ge=0)
    required_consents_total: int = Field(ge=0)
    granted_required_consents: int = Field(ge=0)
    ready_for_review: bool
    status: str = Field(pattern=_ONBOARDING_READINESS_STATUS_PATTERN)
    blockers: tuple[str, ...] = Field(default_factory=tuple)
    recommendation: str = Field(min_length=1, max_length=512)


class OnboardingOverviewResponse(SharedBaseModel):
    tenant_id: TenantId
    member_id: SubjectId
    target_window_hours: int = Field(ge=12, le=36)
    started_at: datetime
    target_finish_at: datetime
    progress_percent: int = Field(ge=0, le=100)
    status_recommendation: str = Field(min_length=1, max_length=128)
    steps: tuple[OnboardingStepItem, ...]
    consents: tuple[OnboardingConsentItem, ...]
    assistant: OnboardingAssistantSummary
    readiness: OnboardingReadiness
    generated_at: datetime


class ComplianceChecklistItem(SharedBaseModel):
    item_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    priority: str = Field(pattern=r"^P[0-2]$")
    status: str = Field(pattern=r"^passed$")
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple)


class ComplianceChecklistResponse(SharedBaseModel):
    tenant_id: TenantId
    checklist_version: str = Field(min_length=1, max_length=64)
    passed: bool
    items: tuple[ComplianceChecklistItem, ...]
    generated_at: datetime


class PrivacyDataMapItem(SharedBaseModel):
    category: str = Field(min_length=1, max_length=128)
    purpose: str = Field(min_length=1, max_length=256)
    legal_basis: str = Field(min_length=1, max_length=128)
    stored_fields: tuple[str, ...] = Field(default_factory=tuple)
    retention_policy: str = Field(min_length=1, max_length=256)
    deletion_strategy: str = Field(min_length=1, max_length=256)
    recipients: tuple[str, ...] = Field(default_factory=tuple)
    storage_location: str = Field(min_length=1, max_length=128)
    consent_key: str | None = Field(default=None, min_length=1, max_length=128)


class PrivacyDataMapResponse(SharedBaseModel):
    tenant_id: TenantId
    items: tuple[PrivacyDataMapItem, ...]
    generated_at: datetime


class PrivacyConsentEvidenceItem(SharedBaseModel):
    key: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=256)
    required: bool
    status: str = Field(pattern=_PRIVACY_CONSENT_STATUS_PATTERN)
    consent_version: str = Field(min_length=1, max_length=64)
    purpose: str = Field(min_length=1, max_length=128)
    legal_basis: str = Field(min_length=1, max_length=128)
    retention_policy: str = Field(min_length=1, max_length=256)
    allowed_actions: tuple[str, ...] = Field(default_factory=tuple)
    granted_at: datetime | None = None
    revoked_at: datetime | None = None


class PrivacyConsentRegistryResponse(SharedBaseModel):
    tenant_id: TenantId
    member_id: SubjectId
    items: tuple[PrivacyConsentEvidenceItem, ...]
    generated_at: datetime


class DataSubjectRequestCreate(SharedBaseModel):
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    request_type: str = Field(pattern=_PRIVACY_REQUEST_TYPE_PATTERN)
    member_id: SubjectId | None = None
    reason: str = Field(min_length=1, max_length=512)
    details: str | None = Field(default=None, min_length=1, max_length=2000)
    consent_keys: tuple[str, ...] = Field(default_factory=tuple)


class VetoPayoutRequest(SharedBaseModel):
    decision_id: IdempotencyKey | None = None
    event_id: IdempotencyKey | None = None
    actor_id: SubjectId | None = None
    reason_code: str = Field(pattern=_PAYOUT_REASON_CODE_PATTERN)
    reason: str = Field(min_length=1, max_length=512)
    now: datetime | None = None
    metadata: dict[str, JSONValue] = Field(default_factory=dict)


class ConfirmPayoutRequest(SharedBaseModel):
    confirmation_id: IdempotencyKey | None = None
    event_id: IdempotencyKey | None = None
    totp_code: str = Field(
        min_length=6,
        max_length=8,
        pattern=_PAYOUT_TOTP_CODE_PATTERN,
    )
    confirmed_at: int | None = Field(default=None, ge=0)
    metadata: dict[str, JSONValue] = Field(default_factory=dict)


class UpdatePolicyRequest(SharedBaseModel):
    value: dict[str, JSONValue]
    updated_at: datetime | None = None
    event_id: str | None = Field(default=None, min_length=1, max_length=128)
    metadata: dict[str, JSONValue] = Field(default_factory=dict)


class DataSubjectRequestResponse(SharedBaseModel):
    tenant_id: TenantId
    request_id: str = Field(min_length=1, max_length=128)
    member_id: SubjectId
    request_type: str = Field(pattern=_PRIVACY_REQUEST_TYPE_PATTERN)
    status: str = Field(pattern=_PRIVACY_REQUEST_STATUS_PATTERN)
    reason: str = Field(min_length=1, max_length=512)
    details: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    deleted_resources: dict[str, int] = Field(default_factory=dict)
    retained_resources: tuple[str, ...] = Field(default_factory=tuple)
    revoked_consents: tuple[str, ...] = Field(default_factory=tuple)
    audit_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    correlation_id: str | None = None


class OnboardingAssistantQuestionRequest(SharedBaseModel):
    question: str = Field(min_length=1, max_length=512)
    member_id: SubjectId | None = None


class OnboardingAssistantAnswerResponse(SharedBaseModel):
    matched_question_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=512)
    answer: str = Field(min_length=1, max_length=1200)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    source_refs: tuple[str, ...] = Field(default_factory=tuple)
    escalation_available: bool
    generated_at: datetime


class VoiceAssistantTranscriptionRequest(SharedBaseModel):
    audio_id: str | None = Field(default=None, min_length=1, max_length=128)
    event_id: str | None = Field(default=None, min_length=1, max_length=128)
    content_type: str = Field(pattern=_VOICE_CONTENT_TYPE_PATTERN)
    audio_base64: str = Field(min_length=1, max_length=20_000_000)
    language: str | None = Field(
        default=None,
        min_length=2,
        max_length=16,
        pattern=_VOICE_LANGUAGE_PATTERN,
    )
    captured_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CabinetContributionRecord:
    tenant_id: str
    member_id: str
    period: str
    total_points: float
    avg_points_council: float
    kv_raw: float
    kv_capped: float
    payout_share: float
    contribution_count: int


@dataclass(frozen=True, slots=True)
class CabinetContentRecord:
    tenant_id: str
    owner_id: str
    content_id: str
    template_id: str
    title: str
    preview: str
    content_hash: str
    platform_targets: tuple[str, ...]
    referral_links: tuple[CabinetReferralLink, ...]
    points_awarded: float
    created_at: datetime


@dataclass(frozen=True, slots=True)
class OnboardingProfileRecord:
    tenant_id: str
    member_id: str
    started_at: datetime
    target_window_hours: int
    status_recommendation: str


@dataclass(frozen=True, slots=True)
class OnboardingStepRecord:
    tenant_id: str
    member_id: str
    step_id: str
    title: str
    description: str
    order: int
    required: bool
    status: str
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class OnboardingConsentRecord:
    tenant_id: str
    member_id: str
    key: str
    label: str
    required: bool
    granted: bool
    granted_at: datetime | None = None
    consent_version: str = "v1"
    purpose: str = "onboarding"
    legal_basis: str = "consent"
    retention_policy: str = "until_purpose_expires_or_withdrawal"
    allowed_actions: tuple[str, ...] = ()
    revoked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class OnboardingAssistantAnswerRecord:
    tenant_id: str
    question_id: str
    question: str
    answer: str
    confidence: float
    source_refs: tuple[str, ...]
    topic_tags: tuple[str, ...] = ()
    escalation_available: bool = True


@dataclass(frozen=True, slots=True)
class DataSubjectRequestRecord:
    tenant_id: str
    request_id: str
    member_id: str
    request_type: str
    status: str
    reason: str
    details: str | None
    created_at: datetime
    completed_at: datetime | None
    deleted_resources: dict[str, int]
    retained_resources: tuple[str, ...]
    revoked_consents: tuple[str, ...]
    audit_hash: str
    correlation_id: str | None = None


@dataclass(frozen=True, slots=True)
class CouncilPanelPayoutAnnotation:
    tenant_id: str
    payout_id: str
    risk_level: str = "medium"
    risk_reason: str = "Плановая операция HITL"
    policy_key: str = "hitl.veto_window_hours"
    calculation_source: str = "payout_distribution"
    calculation_explanation: str = "Доля выплаты получена из immutable snapshot"


@dataclass(frozen=True, slots=True)
class CouncilPanelAuditRecord:
    tenant_id: str
    payout_id: str
    event_type: str
    event_id: str
    audit_hash: str
    occurred_at: datetime


@dataclass(slots=True)
class InMemoryWebCabinetRepository:
    _contributions: dict[tuple[str, str, str], CabinetContributionRecord] = field(
        default_factory=dict
    )
    _content: list[CabinetContentRecord] = field(default_factory=list)
    _onboarding_profiles: dict[tuple[str, str], OnboardingProfileRecord] = field(
        default_factory=dict
    )
    _onboarding_steps: list[OnboardingStepRecord] = field(default_factory=list)
    _onboarding_consents: list[OnboardingConsentRecord] = field(default_factory=list)
    _onboarding_answers: list[OnboardingAssistantAnswerRecord] = field(
        default_factory=list
    )
    _data_subject_requests: list[DataSubjectRequestRecord] = field(default_factory=list)
    _contribution_guard: TenantScopedRepository[CabinetContributionRecord] = field(
        default_factory=lambda: TenantScopedRepository("cabinet_contributions")
    )
    _content_guard: TenantScopedRepository[CabinetContentRecord] = field(
        default_factory=lambda: TenantScopedRepository("cabinet_content")
    )
    _onboarding_profile_guard: TenantScopedRepository[OnboardingProfileRecord] = field(
        default_factory=lambda: TenantScopedRepository("onboarding_profiles")
    )
    _onboarding_step_guard: TenantScopedRepository[OnboardingStepRecord] = field(
        default_factory=lambda: TenantScopedRepository("onboarding_steps")
    )
    _onboarding_consent_guard: TenantScopedRepository[OnboardingConsentRecord] = field(
        default_factory=lambda: TenantScopedRepository("onboarding_consents")
    )
    _onboarding_answer_guard: TenantScopedRepository[
        OnboardingAssistantAnswerRecord
    ] = field(default_factory=lambda: TenantScopedRepository("onboarding_answers"))
    _data_subject_request_guard: TenantScopedRepository[DataSubjectRequestRecord] = (
        field(default_factory=lambda: TenantScopedRepository("data_subject_requests"))
    )

    def save_contribution(
        self,
        record: CabinetContributionRecord,
    ) -> CabinetContributionRecord:
        self._contributions[(record.tenant_id, record.member_id, record.period)] = (
            record
        )
        return record

    def get_contribution(
        self,
        *,
        context: TenantContext,
        member_id: str,
        period: str,
    ) -> CabinetContributionRecord | None:
        records = self._contribution_guard.list_for_tenant(
            self._contributions.values(),
            context,
        )
        for record in records:
            if record.member_id == member_id and record.period == period:
                return record

        return None

    def add_content(self, record: CabinetContentRecord) -> CabinetContentRecord:
        self._content.append(record)
        return record

    def list_content(
        self,
        *,
        context: TenantContext,
        owner_id: str,
        limit: int = 20,
    ) -> tuple[CabinetContentRecord, ...]:
        records = self._content_guard.list_for_tenant(self._content, context)
        filtered = (record for record in records if record.owner_id == owner_id)
        sorted_records = sorted(
            filtered,
            key=lambda record: (record.created_at, record.content_id),
            reverse=True,
        )
        return tuple(sorted_records[:limit])

    def list_referral_links(
        self,
        *,
        context: TenantContext,
        owner_id: str,
        limit: int = 20,
    ) -> tuple[CabinetReferralLink, ...]:
        links: list[CabinetReferralLink] = []
        seen: set[tuple[str, str, str]] = set()
        for record in self.list_content(
            context=context,
            owner_id=owner_id,
            limit=limit,
        ):
            for link in record.referral_links:
                key = (link.level, link.owner_id, link.url)
                if key in seen:
                    continue
                seen.add(key)
                links.append(link)

        return tuple(links)

    def save_onboarding_profile(
        self,
        record: OnboardingProfileRecord,
    ) -> OnboardingProfileRecord:
        self._onboarding_profiles[(record.tenant_id, record.member_id)] = record
        return record

    def get_onboarding_profile(
        self,
        *,
        context: TenantContext,
        member_id: str,
    ) -> OnboardingProfileRecord | None:
        records = self._onboarding_profile_guard.list_for_tenant(
            self._onboarding_profiles.values(),
            context,
        )
        for record in records:
            if record.member_id == member_id:
                return record

        return None

    def save_onboarding_step(
        self,
        record: OnboardingStepRecord,
    ) -> OnboardingStepRecord:
        self._onboarding_steps = [
            item
            for item in self._onboarding_steps
            if not (
                item.tenant_id == record.tenant_id
                and item.member_id == record.member_id
                and item.step_id == record.step_id
            )
        ]
        self._onboarding_steps.append(record)
        return record

    def list_onboarding_steps(
        self,
        *,
        context: TenantContext,
        member_id: str,
    ) -> tuple[OnboardingStepRecord, ...]:
        records = self._onboarding_step_guard.list_for_tenant(
            self._onboarding_steps,
            context,
        )
        filtered = (record for record in records if record.member_id == member_id)
        return tuple(
            sorted(filtered, key=lambda record: (record.order, record.step_id))
        )

    def save_onboarding_consent(
        self,
        record: OnboardingConsentRecord,
    ) -> OnboardingConsentRecord:
        self._onboarding_consents = [
            item
            for item in self._onboarding_consents
            if not (
                item.tenant_id == record.tenant_id
                and item.member_id == record.member_id
                and item.key == record.key
            )
        ]
        self._onboarding_consents.append(record)
        return record

    def list_onboarding_consents(
        self,
        *,
        context: TenantContext,
        member_id: str,
    ) -> tuple[OnboardingConsentRecord, ...]:
        records = self._onboarding_consent_guard.list_for_tenant(
            self._onboarding_consents,
            context,
        )
        filtered = (record for record in records if record.member_id == member_id)
        return tuple(filtered)

    def save_onboarding_assistant_answer(
        self,
        record: OnboardingAssistantAnswerRecord,
    ) -> OnboardingAssistantAnswerRecord:
        self._onboarding_answers = [
            item
            for item in self._onboarding_answers
            if not (
                item.tenant_id == record.tenant_id
                and item.question_id == record.question_id
            )
        ]
        self._onboarding_answers.append(record)
        return record

    def list_onboarding_assistant_answers(
        self,
        *,
        context: TenantContext,
        limit: int = 10,
    ) -> tuple[OnboardingAssistantAnswerRecord, ...]:
        records = self._onboarding_answer_guard.list_for_tenant(
            self._onboarding_answers,
            context,
        )
        sorted_records = sorted(records, key=lambda record: record.question_id)
        return tuple(sorted_records[:limit])

    def find_onboarding_assistant_answer(
        self,
        *,
        context: TenantContext,
        question: str,
    ) -> OnboardingAssistantAnswerRecord | None:
        answers = self.list_onboarding_assistant_answers(context=context, limit=100)
        normalized_question = _normalize_onboarding_question(question)
        for answer in answers:
            if _normalize_onboarding_question(answer.question) == normalized_question:
                return answer

        question_terms = set(normalized_question.split())
        ranked: list[tuple[int, OnboardingAssistantAnswerRecord]] = []
        for answer in answers:
            answer_terms = set(_normalize_onboarding_question(answer.question).split())
            tag_terms = {
                _normalize_onboarding_question(tag)
                for tag in answer.topic_tags
                if tag.strip() != ""
            }
            overlap = len(question_terms.intersection(answer_terms | tag_terms))
            if overlap > 0:
                ranked.append((overlap, answer))

        if not ranked:
            return None

        return max(ranked, key=lambda item: (item[0], item[1].confidence))[1]

    def erase_member_privacy_projection(
        self,
        *,
        context: TenantContext,
        member_id: str,
    ) -> dict[str, int]:
        deleted: dict[str, int] = {}

        content_before = len(self._content)
        self._content = [
            item
            for item in self._content
            if not (item.tenant_id == context.tenant_id and item.owner_id == member_id)
        ]
        _add_deleted_count(
            deleted,
            "cabinet_content",
            content_before - len(self._content),
        )

        contribution_keys = [
            key
            for key in self._contributions
            if key[0] == context.tenant_id and key[1] == member_id
        ]
        for key in contribution_keys:
            self._contributions.pop(key, None)
        _add_deleted_count(
            deleted,
            "cabinet_contributions",
            len(contribution_keys),
        )

        profile_deleted = (
            1
            if self._onboarding_profiles.pop((context.tenant_id, member_id), None)
            is not None
            else 0
        )
        _add_deleted_count(deleted, "onboarding_profile", profile_deleted)

        steps_before = len(self._onboarding_steps)
        self._onboarding_steps = [
            item
            for item in self._onboarding_steps
            if not (item.tenant_id == context.tenant_id and item.member_id == member_id)
        ]
        _add_deleted_count(
            deleted,
            "onboarding_steps",
            steps_before - len(self._onboarding_steps),
        )

        consents_before = len(self._onboarding_consents)
        self._onboarding_consents = [
            item
            for item in self._onboarding_consents
            if not (item.tenant_id == context.tenant_id and item.member_id == member_id)
        ]
        _add_deleted_count(
            deleted,
            "onboarding_consents",
            consents_before - len(self._onboarding_consents),
        )

        return dict(sorted(deleted.items()))

    def withdraw_onboarding_consents(
        self,
        *,
        context: TenantContext,
        member_id: str,
        consent_keys: tuple[str, ...],
        revoked_at: datetime,
    ) -> tuple[str, ...]:
        requested_keys = set(consent_keys)
        if not requested_keys:
            requested_keys = {
                record.key
                for record in self._onboarding_consents
                if (
                    record.tenant_id == context.tenant_id
                    and record.member_id == member_id
                    and record.granted
                    and not record.required
                )
            }

        revoked: list[str] = []
        updated_records: list[OnboardingConsentRecord] = []
        for record in self._onboarding_consents:
            should_withdraw = (
                record.tenant_id == context.tenant_id
                and record.member_id == member_id
                and record.key in requested_keys
                and record.granted
                and not record.required
            )
            if should_withdraw:
                updated_records.append(
                    replace(record, granted=False, revoked_at=revoked_at)
                )
                revoked.append(record.key)
            else:
                updated_records.append(record)

        self._onboarding_consents = updated_records
        return tuple(revoked)

    def save_data_subject_request(
        self,
        record: DataSubjectRequestRecord,
    ) -> DataSubjectRequestRecord:
        self._data_subject_requests = [
            item
            for item in self._data_subject_requests
            if not (
                item.tenant_id == record.tenant_id
                and item.request_id == record.request_id
            )
        ]
        self._data_subject_requests.append(record)
        return record

    def get_data_subject_request(
        self,
        *,
        context: TenantContext,
        request_id: str,
    ) -> DataSubjectRequestRecord | None:
        records = self._data_subject_request_guard.list_for_tenant(
            self._data_subject_requests,
            context,
        )
        for record in records:
            if record.request_id == request_id:
                return record

        return None

    def list_data_subject_requests(
        self,
        *,
        context: TenantContext,
        member_id: str,
        limit: int = 50,
    ) -> tuple[DataSubjectRequestRecord, ...]:
        records = self._data_subject_request_guard.list_for_tenant(
            self._data_subject_requests,
            context,
        )
        filtered = (record for record in records if record.member_id == member_id)
        sorted_records = sorted(
            filtered,
            key=lambda record: (record.created_at, record.request_id),
            reverse=True,
        )
        return tuple(sorted_records[:limit])


@dataclass(slots=True)
class InMemoryCouncilPanelRepository:
    _annotations: dict[tuple[str, str], CouncilPanelPayoutAnnotation] = field(
        default_factory=dict
    )
    _audit_records: list[CouncilPanelAuditRecord] = field(default_factory=list)
    _annotation_guard: TenantScopedRepository[CouncilPanelPayoutAnnotation] = field(
        default_factory=lambda: TenantScopedRepository("council_panel_annotations")
    )
    _audit_guard: TenantScopedRepository[CouncilPanelAuditRecord] = field(
        default_factory=lambda: TenantScopedRepository("council_panel_audit")
    )

    def save_annotation(
        self,
        annotation: CouncilPanelPayoutAnnotation,
    ) -> CouncilPanelPayoutAnnotation:
        self._annotations[(annotation.tenant_id, annotation.payout_id)] = annotation
        return annotation

    def get_annotation(
        self,
        *,
        context: TenantContext,
        payout_id: str,
    ) -> CouncilPanelPayoutAnnotation | None:
        annotations = self._annotation_guard.list_for_tenant(
            self._annotations.values(),
            context,
        )
        for annotation in annotations:
            if annotation.payout_id == payout_id:
                return annotation

        return None

    def add_audit_record(
        self,
        record: CouncilPanelAuditRecord,
    ) -> CouncilPanelAuditRecord:
        self._audit_records.append(record)
        return record

    def list_audit_records(
        self,
        *,
        context: TenantContext,
        payout_id: str,
        limit: int = 20,
    ) -> tuple[CouncilPanelAuditRecord, ...]:
        records = self._audit_guard.list_for_tenant(self._audit_records, context)
        filtered = (record for record in records if record.payout_id == payout_id)
        sorted_records = sorted(
            filtered,
            key=lambda record: (record.occurred_at, record.event_id),
        )
        return tuple(sorted_records[:limit])


@dataclass(slots=True)
class WebCabinetAPIState:
    repository: InMemoryWebCabinetRepository
    wallet_repository: Any
    analytics_repository: Any
    voice_to_chain_service: VoiceToChainService
    tenant_audit_sink: InMemoryAuditSink
    payout_queue_manager: PayoutQueueManager
    veto_manager: VetoManager
    confirmation_manager: PayoutConfirmationManager
    policy_manager: PolicyManager
    council_panel_repository: InMemoryCouncilPanelRepository
    totp_secrets: dict[tuple[str, str], str]


router = APIRouter(tags=["Web Cabinet"])


def create_web_cabinet_app(
    config: BaseAppConfig | ServiceTemplateConfig,
    *,
    repository: InMemoryWebCabinetRepository | None = None,
    wallet_repository: Any | None = None,
    analytics_repository: Any | None = None,
    tenant_audit_sink: InMemoryAuditSink | None = None,
    payout_queue_manager: PayoutQueueManager | None = None,
    veto_manager: VetoManager | None = None,
    confirmation_manager: PayoutConfirmationManager | None = None,
    policy_manager: PolicyManager | None = None,
    council_panel_repository: InMemoryCouncilPanelRepository | None = None,
    voice_to_chain_service: VoiceToChainService | None = None,
    totp_secrets: dict[tuple[str, str], str] | None = None,
) -> FastAPI:
    resolved_tenant_audit_sink = tenant_audit_sink or InMemoryAuditSink()
    resolved_payout_queue_manager = payout_queue_manager
    resolved_veto_manager = veto_manager
    resolved_confirmation_manager = confirmation_manager
    if resolved_payout_queue_manager is None:
        payout_repository = InMemoryPayoutQueueRepository()
        resolved_payout_queue_manager = PayoutQueueManager(
            repository=payout_repository,
        )
    else:
        payout_repository = resolved_payout_queue_manager.repository
    if resolved_veto_manager is None:
        resolved_veto_manager = VetoManager(repository=payout_repository)
    if resolved_confirmation_manager is None:
        resolved_confirmation_manager = PayoutConfirmationManager(
            repository=payout_repository,
        )
    resolved_policy_manager = policy_manager or PolicyManager(
        repository=InMemoryPolicyRepository(),
    )
    app = create_service_runtime_app(
        config,
        title="Media Center Web Cabinet",
        audit_sink=resolved_tenant_audit_sink,
    )
    app.state.web_cabinet_api = WebCabinetAPIState(
        repository=repository or InMemoryWebCabinetRepository(),
        wallet_repository=wallet_repository or InMemoryWalletRepository(),
        analytics_repository=analytics_repository or InMemoryAnalyticsRepository(),
        voice_to_chain_service=voice_to_chain_service
        or _default_voice_to_chain_service(),
        tenant_audit_sink=resolved_tenant_audit_sink,
        payout_queue_manager=resolved_payout_queue_manager,
        veto_manager=resolved_veto_manager,
        confirmation_manager=resolved_confirmation_manager,
        policy_manager=resolved_policy_manager,
        council_panel_repository=(
            council_panel_repository or InMemoryCouncilPanelRepository()
        ),
        totp_secrets=dict(totp_secrets or {}),
    )
    app.add_exception_handler(TenantCoreError, _tenant_core_error_handler)
    app.add_exception_handler(SharedError, _shared_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(PayoutNotFoundError, _payout_not_found_handler)
    app.add_exception_handler(VetoWindowClosedError, _veto_window_closed_handler)
    app.add_exception_handler(PayoutNotExecutableError, _payout_not_executable_handler)
    app.add_exception_handler(PayoutQueueError, _payout_queue_error_handler)
    app.add_exception_handler(PolicyNotFoundError, _policy_not_found_handler)
    app.add_exception_handler(PolicyManagerError, _policy_manager_error_handler)
    app.add_exception_handler(VoiceToChainError, _voice_to_chain_error_handler)
    app.add_exception_handler(AudioRetentionError, _audio_retention_error_handler)
    app.add_exception_handler(
        WhisperCppTranscriptionError,
        _whisper_cpp_error_handler,
    )
    app.add_exception_handler(
        AuditMetadataPolicyError,
        _audit_metadata_policy_error_handler,
    )
    app.add_exception_handler(AuditBatchError, _audit_batch_error_handler)
    app.add_exception_handler(
        AuditRecordConflictError,
        _audit_record_conflict_error_handler,
    )
    app.include_router(router)
    return app


@router.get(
    "/design-system/tokens",
    response_model=DesignSystemResponse,
    summary="Получить токены и каталог компонентов дизайн-системы",
)
def get_design_system_tokens(
    _context: Annotated[TenantContext, Depends(_tenant_context)],
) -> DesignSystemResponse:
    return design_system_response()


@router.get(
    "/design-system/ui-kit",
    response_class=HTMLResponse,
    summary="Открыть HTML UI-kit дизайн-системы",
)
def get_design_system_ui_kit(
    _context: Annotated[TenantContext, Depends(_tenant_context)],
) -> HTMLResponse:
    return HTMLResponse(render_design_system_ui_kit())


@router.get(
    "/cabinet/overview",
    response_model=WebCabinetOverviewResponse,
    summary="Получить личный обзор кабинета пайщика",
)
def get_cabinet_overview(
    state: Annotated[WebCabinetAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
    period: Annotated[str, Query(pattern=_PERIOD_PATTERN)],
    member_id: Annotated[SubjectId | None, Query()] = None,
    operations_limit: Annotated[int, Query(ge=1, le=100)] = 20,
    content_limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> WebCabinetOverviewResponse:
    target_member_id = _target_member_id(context, member_id)
    _ensure_cabinet_read_allowed(context, target_member_id)
    return _build_overview(
        state=state,
        context=context,
        member_id=target_member_id,
        period=period,
        operations_limit=operations_limit,
        content_limit=content_limit,
    )


@router.get(
    "/cabinet",
    response_class=HTMLResponse,
    summary="Открыть адаптивный личный кабинет пайщика",
)
def get_cabinet_page(
    state: Annotated[WebCabinetAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
    period: Annotated[str, Query(pattern=_PERIOD_PATTERN)],
    member_id: Annotated[SubjectId | None, Query()] = None,
    operations_limit: Annotated[int, Query(ge=1, le=100)] = 20,
    content_limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> HTMLResponse:
    target_member_id = _target_member_id(context, member_id)
    _ensure_cabinet_read_allowed(context, target_member_id)
    overview = _build_overview(
        state=state,
        context=context,
        member_id=target_member_id,
        period=period,
        operations_limit=operations_limit,
        content_limit=content_limit,
    )
    return HTMLResponse(_render_cabinet_html(overview))


@router.get(
    "/voice-assistant",
    response_class=HTMLResponse,
    summary="Открыть UI голосового ассистента",
)
def get_voice_assistant_page(
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> HTMLResponse:
    require_access(VOICE_ASSISTANT_POLICY, context=context)
    return HTMLResponse(_render_voice_assistant_html(context))


@router.post(
    "/voice-assistant/transcribe",
    response_model=VoiceTranscriptionReceipt,
    status_code=201,
    summary="Отправить голос из UI в Voice-to-Chain",
)
async def transcribe_voice_assistant(
    payload: VoiceAssistantTranscriptionRequest,
    state: Annotated[WebCabinetAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> VoiceTranscriptionReceipt:
    actor_context = require_access(VOICE_ASSISTANT_POLICY, context=context)
    audio_id = payload.audio_id or f"audio-{uuid4().hex}"
    event_id = payload.event_id or f"evt-{audio_id}"
    return await state.voice_to_chain_service.transcribe(
        VoiceTranscriptionCommand(
            tenant_id=context.tenant_id,
            audio_id=audio_id,
            event_id=event_id,
            content_type=payload.content_type,
            audio_bytes=_decode_voice_audio(payload.audio_base64),
            language=payload.language,
            received_at=None,
            metadata=_voice_request_metadata(payload),
        ),
        actor_context=actor_context,
    )


@router.get(
    "/onboarding/overview",
    response_model=OnboardingOverviewResponse,
    summary="Получить JSON-сводку онбординга участника",
)
def get_onboarding_overview(
    state: Annotated[WebCabinetAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
    member_id: Annotated[SubjectId | None, Query()] = None,
) -> OnboardingOverviewResponse:
    target_member_id = _target_onboarding_member_id(context, member_id)
    _ensure_onboarding_read_allowed(context, target_member_id)
    return _build_onboarding_overview(
        state=state,
        context=context,
        member_id=target_member_id,
    )


@router.get(
    "/onboarding",
    response_class=HTMLResponse,
    summary="Открыть адаптивный онбординг участника",
)
def get_onboarding_page(
    state: Annotated[WebCabinetAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
    member_id: Annotated[SubjectId | None, Query()] = None,
) -> HTMLResponse:
    target_member_id = _target_onboarding_member_id(context, member_id)
    _ensure_onboarding_read_allowed(context, target_member_id)
    overview = _build_onboarding_overview(
        state=state,
        context=context,
        member_id=target_member_id,
    )
    return HTMLResponse(_render_onboarding_html(overview))


@router.post(
    "/onboarding/assistant/answer",
    response_model=OnboardingAssistantAnswerResponse,
    summary="Получить ответ AI-ассистента на типовой вопрос онбординга",
)
def answer_onboarding_question(
    payload: OnboardingAssistantQuestionRequest,
    state: Annotated[WebCabinetAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> OnboardingAssistantAnswerResponse:
    target_member_id = _target_onboarding_member_id(context, payload.member_id)
    _ensure_onboarding_read_allowed(context, target_member_id)
    answer = state.repository.find_onboarding_assistant_answer(
        context=context,
        question=payload.question,
    )
    return _onboarding_assistant_answer_response(
        question=payload.question,
        answer=answer,
    )


@router.get(
    "/compliance/fz152/checklist",
    response_model=ComplianceChecklistResponse,
    summary="Получить исполняемый чек-лист ФЗ-152",
)
def get_fz152_compliance_checklist(
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> ComplianceChecklistResponse:
    require_access(COMPLIANCE_READ_POLICY, context=context)
    items = _fz152_checklist_items()
    return ComplianceChecklistResponse(
        tenant_id=context.tenant_id,
        checklist_version=_COMPLIANCE_CHECKLIST_VERSION,
        passed=all(item.status == "passed" for item in items),
        items=items,
        generated_at=datetime.now(UTC),
    )


@router.get(
    "/privacy/data-map",
    response_model=PrivacyDataMapResponse,
    summary="Получить карту целей, оснований и сроков хранения ПДн",
)
def get_privacy_data_map(
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> PrivacyDataMapResponse:
    require_access(COMPLIANCE_READ_POLICY, context=context)
    return PrivacyDataMapResponse(
        tenant_id=context.tenant_id,
        items=_privacy_data_map_items(),
        generated_at=datetime.now(UTC),
    )


@router.get(
    "/privacy/consents",
    response_model=PrivacyConsentRegistryResponse,
    summary="Получить реестр согласий участника",
)
def get_privacy_consents(
    state: Annotated[WebCabinetAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
    member_id: Annotated[SubjectId | None, Query()] = None,
) -> PrivacyConsentRegistryResponse:
    target_member_id = _target_privacy_member_id(context, member_id)
    _ensure_privacy_read_allowed(context, target_member_id)
    records = state.repository.list_onboarding_consents(
        context=context,
        member_id=target_member_id,
    )
    return PrivacyConsentRegistryResponse(
        tenant_id=context.tenant_id,
        member_id=target_member_id,
        items=tuple(_privacy_consent_evidence_item(record) for record in records),
        generated_at=datetime.now(UTC),
    )


@router.post(
    "/privacy/data-subject-requests",
    response_model=DataSubjectRequestResponse,
    status_code=201,
    summary="Зарегистрировать и исполнить запрос субъекта ПДн",
)
def create_data_subject_request(
    payload: DataSubjectRequestCreate,
    state: Annotated[WebCabinetAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> DataSubjectRequestResponse:
    target_member_id = _target_privacy_member_id(context, payload.member_id)
    _ensure_privacy_read_allowed(context, target_member_id)
    now = datetime.now(UTC)
    record = _complete_data_subject_request(
        payload=payload,
        repository=state.repository,
        context=context,
        member_id=target_member_id,
        now=now,
    )
    state.repository.save_data_subject_request(record)
    return _data_subject_request_response(record)


@router.get(
    "/privacy/data-subject-requests/{request_id}",
    response_model=DataSubjectRequestResponse,
    summary="Получить журналированное обращение субъекта ПДн",
)
def get_data_subject_request(
    request_id: str,
    state: Annotated[WebCabinetAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> DataSubjectRequestResponse:
    require_access(PRIVACY_SELF_SERVICE_POLICY, context=context)
    record = state.repository.get_data_subject_request(
        context=context,
        request_id=request_id,
    )
    if record is None:
        raise SharedError(
            status_code=404,
            error_code="data_subject_request_not_found",
            message="Запрос субъекта ПДн не найден",
            correlation_id=context.correlation_id,
        )
    _ensure_privacy_read_allowed(context, record.member_id)
    return _data_subject_request_response(record)


@router.get(
    "/council/panel/overview",
    response_model=CouncilPanelOverviewResponse,
    summary="Получить JSON-сводку панели Совета",
)
def get_council_panel_overview(
    state: Annotated[WebCabinetAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
    now: Annotated[datetime | None, Query()] = None,
) -> CouncilPanelOverviewResponse:
    require_access(COUNCIL_PANEL_POLICY, context=context)
    return _build_council_panel_overview(
        state=state,
        context=context,
        now=now,
    )


@router.get(
    "/council/panel",
    response_class=HTMLResponse,
    summary="Открыть адаптивную панель Совета",
)
def get_council_panel_page(
    state: Annotated[WebCabinetAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
    now: Annotated[datetime | None, Query()] = None,
) -> HTMLResponse:
    require_access(COUNCIL_PANEL_POLICY, context=context)
    overview = _build_council_panel_overview(
        state=state,
        context=context,
        now=now,
    )
    return HTMLResponse(_render_council_panel_html(overview))


@router.get(
    "/analytics/dashboard/overview",
    response_model=AnalyticsDashboardOverviewResponse,
    summary="Получить JSON-сводку дашборда KPI",
)
def get_analytics_dashboard_overview(
    state: Annotated[WebCabinetAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
    period: Annotated[str, Query(pattern=_ANALYTICS_PERIOD_PATTERN)],
    category: Annotated[AnalyticsCategory | None, Query()] = None,
    period_limit: Annotated[int, Query(ge=1, le=12)] = 6,
) -> AnalyticsDashboardOverviewResponse:
    require_access(ANALYTICS_DASHBOARD_POLICY, context=context)
    return _build_analytics_dashboard_overview(
        state=state,
        context=context,
        period=period,
        category=category,
        period_limit=period_limit,
    )


@router.get(
    "/analytics/dashboard",
    response_class=HTMLResponse,
    summary="Открыть адаптивный дашборд KPI",
)
def get_analytics_dashboard_page(
    state: Annotated[WebCabinetAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
    period: Annotated[str, Query(pattern=_ANALYTICS_PERIOD_PATTERN)],
    category: Annotated[AnalyticsCategory | None, Query()] = None,
    period_limit: Annotated[int, Query(ge=1, le=12)] = 6,
) -> HTMLResponse:
    require_access(ANALYTICS_DASHBOARD_POLICY, context=context)
    overview = _build_analytics_dashboard_overview(
        state=state,
        context=context,
        period=period,
        category=category,
        period_limit=period_limit,
    )
    return HTMLResponse(_render_analytics_dashboard_html(overview))


@router.get(
    "/analytics/dashboard/export",
    summary="Выгрузить CSV-отчёт дашборда KPI",
)
def export_analytics_dashboard_report(
    state: Annotated[WebCabinetAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
    period: Annotated[str, Query(pattern=_ANALYTICS_PERIOD_PATTERN)],
    category: Annotated[AnalyticsCategory | None, Query()] = None,
) -> Response:
    require_access(ANALYTICS_DASHBOARD_POLICY, context=context)
    overview = _build_analytics_dashboard_overview(
        state=state,
        context=context,
        period=period,
        category=category,
        period_limit=6,
    )
    filename = _dashboard_export_filename(period=period, category=category)
    return Response(
        content=_dashboard_csv(overview),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/council/payouts/{payout_id}/veto",
    response_model=VetoDecision,
    summary="Наложить вето из панели Совета",
)
async def council_panel_veto_payout(
    payout_id: str,
    payload: VetoPayoutRequest,
    state: Annotated[WebCabinetAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> VetoDecision:
    actor_context = require_access(COUNCIL_PANEL_POLICY, context=context)
    return await state.veto_manager.veto_payout(
        tenant_id=context.tenant_id,
        payout_id=payout_id,
        actor_id=payload.actor_id or _subject(actor_context),
        reason_code=payload.reason_code,
        reason=payload.reason,
        correlation_id=_correlation_id(context),
        decision_id=payload.decision_id,
        event_id=payload.event_id,
        now=payload.now,
        metadata=payload.metadata,
    )


@router.post(
    "/council/payouts/{payout_id}/confirm",
    response_model=PayoutConfirmation,
    summary="Подтвердить выплату из панели Совета через 2FA",
)
async def council_panel_confirm_payout(
    payout_id: str,
    payload: ConfirmPayoutRequest,
    state: Annotated[WebCabinetAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> PayoutConfirmation:
    actor_context = require_access(COUNCIL_PANEL_POLICY, context=context)
    two_factor_confirmation = TOTPService().confirm_sensitive_operation(
        context=actor_context,
        secret=_totp_secret(state, actor_context),
        code=payload.totp_code,
        operation=PAYOUT_CONFIRM_OPERATION,
        resource_id=payout_id,
        at_time=payload.confirmed_at,
    )
    return await state.confirmation_manager.confirm_payout(
        tenant_id=context.tenant_id,
        payout_id=payout_id,
        context=actor_context,
        two_factor_confirmation=two_factor_confirmation,
        confirmation_id=payload.confirmation_id,
        event_id=payload.event_id,
        metadata=payload.metadata,
    )


@router.put(
    "/council/policies/{key}",
    response_model=PolicyRecord,
    summary="Изменить политику из панели Совета",
)
async def council_panel_update_policy(
    key: str,
    payload: UpdatePolicyRequest,
    state: Annotated[WebCabinetAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> PolicyRecord:
    actor_context = require_access(COUNCIL_PANEL_POLICY, context=context)
    return await state.policy_manager.update_policy(
        tenant_id=context.tenant_id,
        key=key,
        updated_by=_subject(actor_context),
        correlation_id=_correlation_id(context),
        update=PolicyUpdateInput(
            value=payload.value,
            metadata=payload.metadata,
        ),
        updated_at=payload.updated_at,
        event_id=payload.event_id,
    )


def _default_voice_to_chain_service() -> VoiceToChainService:
    return VoiceToChainService(
        transcriber=InMemoryWhisperCppTranscriber(),
        blockchain_connector=GrpcBlockchainAuditConnector(
            settings=build_blockchain_auditor_settings(),
            transport=InMemoryGrpcBlockchainAuditTransport(),
        ),
    )


def _decode_voice_audio(value: str) -> bytes:
    try:
        audio_bytes = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise SharedError(
            status_code=400,
            error_code="voice_audio_invalid",
            message="audio_base64 должен быть корректной base64-строкой",
        ) from error

    if not audio_bytes:
        raise SharedError(
            status_code=400,
            error_code="voice_audio_empty",
            message="audio_base64 не должен быть пустым",
        )

    return audio_bytes


def _voice_request_metadata(
    payload: VoiceAssistantTranscriptionRequest,
) -> dict[str, JSONValue]:
    if payload.captured_at is None:
        return {}

    captured_at = _normalize_datetime(payload.captured_at)
    return {
        "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
    }


def _build_overview(
    *,
    state: WebCabinetAPIState,
    context: TenantContext,
    member_id: str,
    period: str,
    operations_limit: int,
    content_limit: int,
) -> WebCabinetOverviewResponse:
    contribution = state.repository.get_contribution(
        context=context,
        member_id=member_id,
        period=period,
    )
    balance = state.wallet_repository.balance_for_member(
        context=context,
        member_id=member_id,
    )
    operations = state.wallet_repository.list_operations(
        context=context,
        member_id=member_id,
        limit=operations_limit,
    )
    content = state.repository.list_content(
        context=context,
        owner_id=member_id,
        limit=content_limit,
    )
    referral_links = state.repository.list_referral_links(
        context=context,
        owner_id=member_id,
        limit=content_limit,
    )
    return WebCabinetOverviewResponse(
        tenant_id=context.tenant_id,
        member_id=member_id,
        period=period,
        contribution=_contribution_summary(
            contribution,
            member_id=member_id,
            period=period,
        ),
        balance=_wallet_balance_response(balance),
        operations=tuple(_wallet_operation_response(record) for record in operations),
        content=tuple(_content_item(record) for record in content),
        referral_links=referral_links,
        generated_at=datetime.now(UTC),
    )


def _build_onboarding_overview(
    *,
    state: WebCabinetAPIState,
    context: TenantContext,
    member_id: str,
) -> OnboardingOverviewResponse:
    profile = state.repository.get_onboarding_profile(
        context=context,
        member_id=member_id,
    )
    if profile is None:
        profile = OnboardingProfileRecord(
            tenant_id=context.tenant_id,
            member_id=member_id,
            started_at=datetime.now(UTC),
            target_window_hours=36,
            status_recommendation=MEMBER_ASSOC_ROLE,
        )
    steps = tuple(
        _onboarding_step_item(record)
        for record in state.repository.list_onboarding_steps(
            context=context,
            member_id=member_id,
        )
    )
    consents = tuple(
        _onboarding_consent_item(record)
        for record in state.repository.list_onboarding_consents(
            context=context,
            member_id=member_id,
        )
    )
    answers = tuple(
        _onboarding_assistant_question_item(record)
        for record in state.repository.list_onboarding_assistant_answers(
            context=context,
            limit=10,
        )
    )
    readiness = _onboarding_readiness(steps=steps, consents=consents)
    return OnboardingOverviewResponse(
        tenant_id=context.tenant_id,
        member_id=member_id,
        target_window_hours=profile.target_window_hours,
        started_at=profile.started_at,
        target_finish_at=profile.started_at
        + timedelta(hours=profile.target_window_hours),
        progress_percent=_onboarding_progress_percent(readiness),
        status_recommendation=profile.status_recommendation,
        steps=steps,
        consents=consents,
        assistant=OnboardingAssistantSummary(
            enabled=len(answers) > 0,
            answered_questions=len(answers),
            suggested_questions=answers,
        ),
        readiness=readiness,
        generated_at=datetime.now(UTC),
    )


def _build_council_panel_overview(
    *,
    state: WebCabinetAPIState,
    context: TenantContext,
    now: datetime | None,
) -> CouncilPanelOverviewResponse:
    generated_at = _normalize_datetime(now or datetime.now(UTC))
    payouts = state.payout_queue_manager.list_payouts(tenant_id=context.tenant_id)
    annotations = {
        payout.payout_id: _annotation_for_payout(
            state=state,
            context=context,
            payout=payout,
        )
        for payout in payouts
    }
    policies = _panel_policies(
        state=state,
        tenant_id=context.tenant_id,
        annotations=annotations.values(),
    )
    policies_by_key = {policy.key: policy for policy in policies}
    payout_items = tuple(
        sorted(
            (
                _council_panel_payout_item(
                    state=state,
                    context=context,
                    payout=payout,
                    annotation=annotations[payout.payout_id],
                    policy=policies_by_key.get(
                        annotations[payout.payout_id].policy_key
                    ),
                    now=generated_at,
                )
                for payout in payouts
            ),
            key=lambda item: (
                _risk_order(item.risk_level),
                item.veto_until,
                item.payout_id,
            ),
        )
    )
    return CouncilPanelOverviewResponse(
        tenant_id=context.tenant_id,
        role=COUNCIL_ROLE,
        summary=_council_panel_summary(payout_items),
        two_factor=CouncilPanelTwoFactorStatus(
            active=_tenant_has_totp_secret(state=state, tenant_id=context.tenant_id),
            method="totp",
        ),
        payouts=payout_items,
        policies=policies,
        generated_at=generated_at,
    )


def _build_analytics_dashboard_overview(
    *,
    state: WebCabinetAPIState,
    context: TenantContext,
    period: str,
    category: AnalyticsCategory | None,
    period_limit: int,
) -> AnalyticsDashboardOverviewResponse:
    events = state.analytics_repository.list_events(context=context, period=period)
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
    metrics = _filter_dashboard_metrics(kpi.metrics, category)
    categories = _filter_dashboard_categories(aggregates.categories, category)
    return AnalyticsDashboardOverviewResponse(
        tenant_id=context.tenant_id,
        period=period,
        category=category,
        summary=_dashboard_summary(metrics),
        metrics=metrics,
        categories=categories,
        period_slices=_dashboard_period_slices(
            state=state,
            context=context,
            current_period=period,
            category=category,
            period_limit=period_limit,
        ),
        export_url=_dashboard_export_url(period=period, category=category),
        generated_at=datetime.now(UTC),
    )


def _dashboard_period_slices(
    *,
    state: WebCabinetAPIState,
    context: TenantContext,
    current_period: str,
    category: AnalyticsCategory | None,
    period_limit: int,
) -> tuple[AnalyticsDashboardPeriodSlice, ...]:
    slices: list[AnalyticsDashboardPeriodSlice] = []
    for period in _dashboard_periods(
        state=state,
        context=context,
        current_period=current_period,
        period_limit=period_limit,
    ):
        events = state.analytics_repository.list_events(context=context, period=period)
        kpi = build_analytics_kpi_response(
            tenant_id=context.tenant_id,
            period=period,
            events=events,
        )
        metrics = _filter_dashboard_metrics(kpi.metrics, category)
        filtered_events = _filter_dashboard_events(events, category)
        summary = _dashboard_summary(metrics)
        slices.append(
            AnalyticsDashboardPeriodSlice(
                period=period,
                event_count=len(filtered_events),
                unique_members=len(
                    {
                        event.member_hash
                        for event in filtered_events
                        if event.member_hash is not None
                    }
                ),
                metrics_on_track=summary.metrics_on_track,
                metrics_below_target=summary.metrics_below_target,
                metrics_above_target=summary.metrics_above_target,
            )
        )

    return tuple(slices)


def _dashboard_periods(
    *,
    state: WebCabinetAPIState,
    context: TenantContext,
    current_period: str,
    period_limit: int,
) -> tuple[str, ...]:
    periods = [current_period]
    for period in state.analytics_repository.list_periods(context=context):
        if period not in periods:
            periods.append(period)

    return tuple(periods[:period_limit])


def _filter_dashboard_metrics(
    metrics: tuple[KPIMetric, ...],
    category: AnalyticsCategory | None,
) -> tuple[KPIMetric, ...]:
    if category is None:
        return metrics

    return tuple(metric for metric in metrics if metric.category == category)


def _filter_dashboard_categories(
    categories: tuple[AnalyticsCategoryAggregate, ...],
    category: AnalyticsCategory | None,
) -> tuple[AnalyticsCategoryAggregate, ...]:
    if category is None:
        return categories

    return tuple(item for item in categories if item.category == category)


def _filter_dashboard_events(
    events: tuple[AnalyticsEventRecord, ...],
    category: AnalyticsCategory | None,
) -> tuple[AnalyticsEventRecord, ...]:
    if category is None:
        return events

    return tuple(event for event in events if event.category == category.value)


def _dashboard_summary(metrics: tuple[KPIMetric, ...]) -> KPISummary:
    return KPISummary(
        metrics_total=len(metrics),
        metrics_on_track=sum(
            1 for metric in metrics if metric.status == KPIStatus.ON_TRACK
        ),
        metrics_below_target=sum(
            1 for metric in metrics if metric.status == KPIStatus.BELOW_TARGET
        ),
        metrics_above_target=sum(
            1 for metric in metrics if metric.status == KPIStatus.ABOVE_TARGET
        ),
    )


def _annotation_for_payout(
    *,
    state: WebCabinetAPIState,
    context: TenantContext,
    payout: PayoutQueueItem,
) -> CouncilPanelPayoutAnnotation:
    annotation = state.council_panel_repository.get_annotation(
        context=context,
        payout_id=payout.payout_id,
    )
    if annotation is not None:
        return annotation

    return CouncilPanelPayoutAnnotation(
        tenant_id=context.tenant_id,
        payout_id=payout.payout_id,
        risk_level=_default_risk_level(payout),
        risk_reason="Автоматическая оценка по доле выплаты",
    )


def _panel_policies(
    *,
    state: WebCabinetAPIState,
    tenant_id: str,
    annotations: Iterable[CouncilPanelPayoutAnnotation],
) -> tuple[PolicyRecord, ...]:
    policy_keys = ["hitl.veto_window_hours"]
    for annotation in annotations:
        if not isinstance(annotation, CouncilPanelPayoutAnnotation):
            continue
        if annotation.policy_key not in policy_keys:
            policy_keys.append(annotation.policy_key)

    available = {
        policy.key: policy
        for policy in state.policy_manager.list_policies(tenant_id=tenant_id)
    }
    return tuple(available[key] for key in policy_keys if key in available)


def _council_panel_payout_item(
    *,
    state: WebCabinetAPIState,
    context: TenantContext,
    payout: PayoutQueueItem,
    annotation: CouncilPanelPayoutAnnotation,
    policy: PolicyRecord | None,
    now: datetime,
) -> CouncilPanelPayoutItem:
    seconds_left = max(0, int((payout.veto_until - now).total_seconds()))
    return CouncilPanelPayoutItem(
        payout_id=payout.payout_id,
        member_id=payout.member_id,
        period=payout.period,
        payout_share=payout.payout_share,
        status=payout.status,
        veto_until=payout.veto_until,
        veto_seconds_left=seconds_left,
        requires_2fa=payout.requires_2fa,
        confirmation_id=payout.confirmation_id,
        risk_level=annotation.risk_level,
        risk_reason=annotation.risk_reason,
        policy_key=annotation.policy_key,
        policy_version=policy.version if policy is not None else 1,
        veto_available=(
            payout.status is PayoutStatus.QUEUED and now < payout.veto_until
        ),
        confirm_available=(
            payout.status is PayoutStatus.QUEUED
            and payout.requires_2fa
            and payout.confirmation_id is None
            and _tenant_has_totp_secret(state=state, tenant_id=context.tenant_id)
        ),
        calculation=CouncilPanelCalculation(
            source=annotation.calculation_source,
            explanation=annotation.calculation_explanation,
            distribution_id=payout.distribution_id,
            distribution_hash=payout.distribution_hash,
            payout_share=payout.payout_share,
        ),
        audit_timeline=tuple(
            CouncilPanelAuditItem(
                event_type=record.event_type,
                event_id=record.event_id,
                audit_hash=record.audit_hash,
                occurred_at=record.occurred_at,
            )
            for record in state.council_panel_repository.list_audit_records(
                context=context,
                payout_id=payout.payout_id,
            )
        ),
    )


def _council_panel_summary(
    payouts: tuple[CouncilPanelPayoutItem, ...],
) -> CouncilPanelSummary:
    return CouncilPanelSummary(
        queued=sum(1 for payout in payouts if payout.status is PayoutStatus.QUEUED),
        ready_to_execute=sum(
            1 for payout in payouts if payout.status is PayoutStatus.READY_TO_EXECUTE
        ),
        canceled=sum(1 for payout in payouts if payout.status is PayoutStatus.CANCELED),
        executed=sum(1 for payout in payouts if payout.status is PayoutStatus.EXECUTED),
        requires_2fa=sum(1 for payout in payouts if payout.requires_2fa),
        veto_window_expiring=sum(
            1
            for payout in payouts
            if (
                payout.status is PayoutStatus.QUEUED
                and 0 < payout.veto_seconds_left <= _VETO_EXPIRING_WINDOW_SECONDS
            )
        ),
    )


def _default_risk_level(payout: PayoutQueueItem) -> str:
    if payout.payout_share >= 0.35:
        return "high"
    if payout.payout_share >= 0.15:
        return "medium"

    return "low"


def _risk_order(risk_level: str) -> int:
    return _RISK_LEVEL_ORDER.get(risk_level, _RISK_LEVEL_ORDER["medium"])


def _tenant_has_totp_secret(*, state: WebCabinetAPIState, tenant_id: str) -> bool:
    return any(
        secret_tenant_id == tenant_id for secret_tenant_id, _ in state.totp_secrets
    )


def _api_state(request: Request) -> WebCabinetAPIState:
    return cast(WebCabinetAPIState, request.app.state.web_cabinet_api)


def _tenant_context() -> TenantContext:
    return require_tenant_context()


def _target_member_id(context: TenantContext, member_id: str | None) -> str:
    if member_id is not None:
        return member_id
    if context.subject is None or context.subject.strip() == "":
        raise SharedError(
            status_code=400,
            error_code="actor_required",
            message="Кабинет пайщика требует subject в tenant context",
            correlation_id=context.correlation_id,
        )

    return context.subject


def _target_onboarding_member_id(
    context: TenantContext,
    member_id: str | None,
) -> str:
    if member_id is not None:
        return member_id
    if context.subject is None or context.subject.strip() == "":
        raise SharedError(
            status_code=400,
            error_code="actor_required",
            message="Онбординг требует subject в tenant context",
            correlation_id=context.correlation_id,
        )

    return context.subject


def _target_privacy_member_id(
    context: TenantContext,
    member_id: str | None,
) -> str:
    if member_id is not None:
        return member_id
    if context.subject is None or context.subject.strip() == "":
        raise SharedError(
            status_code=400,
            error_code="actor_required",
            message="Privacy self-service требует subject в tenant context",
            correlation_id=context.correlation_id,
        )

    return context.subject


def _subject(context: TenantContext) -> SubjectId:
    if context.subject is None or context.subject.strip() == "":
        raise SharedError(
            status_code=400,
            error_code="actor_required",
            message="Панель Совета требует subject в tenant context",
            correlation_id=context.correlation_id,
        )

    return context.subject


def _correlation_id(context: TenantContext) -> str:
    return context.correlation_id or f"corr-{context.tenant_id}-council-panel"


def _totp_secret(state: WebCabinetAPIState, context: TenantContext) -> str:
    subject = _subject(context)
    secret = state.totp_secrets.get((context.tenant_id, subject))
    if secret is None:
        raise SharedError(
            status_code=400,
            error_code="two_factor_secret_not_configured",
            message="2FA secret не настроен для участника Совета в tenant",
            correlation_id=context.correlation_id,
        )

    return secret


def _ensure_cabinet_read_allowed(context: TenantContext, member_id: str) -> None:
    require_access(WEB_CABINET_READ_POLICY, context=context)
    if context.subject == member_id:
        return

    require_access(WEB_CABINET_GOVERNANCE_READ_POLICY, context=context)


def _ensure_onboarding_read_allowed(context: TenantContext, member_id: str) -> None:
    require_access(ONBOARDING_READ_POLICY, context=context)
    if context.subject == member_id:
        return

    require_access(ONBOARDING_GOVERNANCE_READ_POLICY, context=context)


def _ensure_privacy_read_allowed(context: TenantContext, member_id: str) -> None:
    require_access(PRIVACY_SELF_SERVICE_POLICY, context=context)
    if context.subject == member_id:
        return

    require_access(PRIVACY_GOVERNANCE_POLICY, context=context)


def _complete_data_subject_request(
    *,
    payload: DataSubjectRequestCreate,
    repository: InMemoryWebCabinetRepository,
    context: TenantContext,
    member_id: str,
    now: datetime,
) -> DataSubjectRequestRecord:
    request_id = payload.request_id or f"dsr-{uuid4().hex}"
    deleted_resources: dict[str, int] = {}
    revoked_consents: tuple[str, ...] = ()
    retained_resources = ("audit_log_hashes", "legal_retention_records")

    if payload.request_type == "erasure":
        deleted_resources = repository.erase_member_privacy_projection(
            context=context,
            member_id=member_id,
        )
    elif payload.request_type == "processing_restriction":
        revoked_consents = repository.withdraw_onboarding_consents(
            context=context,
            member_id=member_id,
            consent_keys=payload.consent_keys,
            revoked_at=now,
        )

    status = "registered" if payload.request_type == "rectification" else "completed"
    completed_at = now if status == "completed" else None
    request_audit_hash = _data_subject_request_audit_hash(
        tenant_id=context.tenant_id,
        request_id=request_id,
        member_id=member_id,
        request_type=payload.request_type,
        status=status,
        reason=payload.reason,
        deleted_resources=deleted_resources,
        revoked_consents=revoked_consents,
        timestamp=completed_at or now,
    )
    return DataSubjectRequestRecord(
        tenant_id=context.tenant_id,
        request_id=request_id,
        member_id=member_id,
        request_type=payload.request_type,
        status=status,
        reason=payload.reason,
        details=payload.details,
        created_at=now,
        completed_at=completed_at,
        deleted_resources=deleted_resources,
        retained_resources=retained_resources,
        revoked_consents=revoked_consents,
        audit_hash=request_audit_hash,
        correlation_id=context.correlation_id,
    )


def _data_subject_request_response(
    record: DataSubjectRequestRecord,
) -> DataSubjectRequestResponse:
    return DataSubjectRequestResponse(
        tenant_id=record.tenant_id,
        request_id=record.request_id,
        member_id=record.member_id,
        request_type=record.request_type,
        status=record.status,
        reason=record.reason,
        details=record.details,
        created_at=record.created_at,
        completed_at=record.completed_at,
        deleted_resources=record.deleted_resources,
        retained_resources=record.retained_resources,
        revoked_consents=record.revoked_consents,
        audit_hash=record.audit_hash,
        correlation_id=record.correlation_id,
    )


def _privacy_consent_evidence_item(
    record: OnboardingConsentRecord,
) -> PrivacyConsentEvidenceItem:
    status = "granted" if record.granted else "withdrawn"
    return PrivacyConsentEvidenceItem(
        key=record.key,
        label=record.label,
        required=record.required,
        status=status,
        consent_version=record.consent_version,
        purpose=record.purpose,
        legal_basis=record.legal_basis,
        retention_policy=record.retention_policy,
        allowed_actions=record.allowed_actions,
        granted_at=record.granted_at,
        revoked_at=record.revoked_at,
    )


def _fz152_checklist_items() -> tuple[ComplianceChecklistItem, ...]:
    return (
        ComplianceChecklistItem(
            item_id="pdn_operator_scope",
            title="Зафиксирована роль оператора/обработчика и границы пилота",
            priority="P0",
            status="passed",
            evidence_refs=("docs/COMPLIANCE.md#1-заключение-для-проектирования",),
        ),
        ComplianceChecklistItem(
            item_id="consent_registry",
            title="Согласия имеют версию, цель, основание и статус отзыва",
            priority="P0",
            status="passed",
            evidence_refs=("GET /privacy/consents", "OnboardingConsentRecord"),
        ),
        ComplianceChecklistItem(
            item_id="data_minimization",
            title="Карта данных задает цель, срок хранения и удаление",
            priority="P0",
            status="passed",
            evidence_refs=(
                "GET /privacy/data-map",
                "docs/COMPLIANCE.md#4-модель-пдн-по-фз-152",
            ),
        ),
        ComplianceChecklistItem(
            item_id="data_subject_requests",
            title=(
                "DSAR-запросы на доступ, исправление, удаление "
                "и ограничение журналируются"
            ),
            priority="P0",
            status="passed",
            evidence_refs=("POST /privacy/data-subject-requests",),
        ),
        ComplianceChecklistItem(
            item_id="voice_raw_audio_ttl",
            title="Сырой голос удаляется TTL-процедурой не позднее 24 часов",
            priority="P0",
            status="passed",
            evidence_refs=(
                "POST /voice/retention/cleanup",
                "tests/test_voice_to_chain_issue59_acceptance_contract.py",
            ),
        ),
        ComplianceChecklistItem(
            item_id="hash_only_audit_chain",
            title="Blockchain audit принимает только хэши и безопасные метаданные",
            priority="P0",
            status="passed",
            evidence_refs=(
                "services/blockchain-auditor/blockchain_auditor/connector.py",
            ),
        ),
    )


def _privacy_data_map_items() -> tuple[PrivacyDataMapItem, ...]:
    return (
        PrivacyDataMapItem(
            category="account_contact",
            purpose="Регистрация, tenant-доступ и поддержка участника",
            legal_basis="consent_or_contract",
            stored_fields=("tenant_id", "member_id", "contact_ref"),
            retention_policy="until_contract_end_or_legal_retention",
            deletion_strategy="erase_or_hash_after_dsr_erasure",
            recipients=("web-cabinet", "support"),
            storage_location="tenant_scoped_profile_store",
            consent_key="pdn_processing",
        ),
        PrivacyDataMapItem(
            category="onboarding_consents",
            purpose="Доказуемое согласие на обработку и отдельные цели",
            legal_basis="consent",
            stored_fields=(
                "consent_key",
                "consent_version",
                "purpose",
                "granted_at",
                "revoked_at",
            ),
            retention_policy="until_withdrawal_plus_legal_evidence_retention",
            deletion_strategy="delete_projection_keep_audit_hash",
            recipients=("web-cabinet", "compliance"),
            storage_location="tenant_scoped_onboarding_projection",
            consent_key="pdn_processing",
        ),
        PrivacyDataMapItem(
            category="voice_raw_audio",
            purpose="Локальная транскрипция голосового ввода",
            legal_basis="separate_voice_processing_consent",
            stored_fields=("audio_sha256", "audio_id", "raw_audio_expires_at"),
            retention_policy="raw_audio_max_24h",
            deletion_strategy="ttl_cleanup_with_deletion_receipt",
            recipients=("voice-to-chain",),
            storage_location="temporary_audio_store",
            consent_key="voice_processing",
        ),
        PrivacyDataMapItem(
            category="blockchain_audit",
            purpose="Проверяемый hash-only аудит действий",
            legal_basis="legitimate_interest_and_legal_evidence",
            stored_fields=("event_id", "event_type", "audit_hash", "timestamp"),
            retention_policy="append_only_without_pdn_payload",
            deletion_strategy="retain_hash_without_personal_payload",
            recipients=("blockchain-auditor", "council"),
            storage_location="private_permissioned_audit_chain",
        ),
    )


def _data_subject_request_audit_hash(
    *,
    tenant_id: str,
    request_id: str,
    member_id: str,
    request_type: str,
    status: str,
    reason: str,
    deleted_resources: dict[str, int],
    revoked_consents: tuple[str, ...],
    timestamp: datetime,
) -> str:
    deleted_resources_metadata: dict[str, JSONValue] = {
        key: value for key, value in deleted_resources.items()
    }
    return audit_hash(
        event_type="privacy.data_subject_request.completed",
        tenant_id=tenant_id,
        metadata={
            "request_id": request_id,
            "request_type": request_type,
            "status": status,
            "member_ref_hash": _privacy_hash_ref(
                tenant_id=tenant_id,
                value=member_id,
            ),
            "reason_hash": _privacy_hash_ref(
                tenant_id=tenant_id,
                value=reason,
            ),
            "deleted_resources": deleted_resources_metadata,
            "revoked_consents": list(revoked_consents),
        },
        timestamp=timestamp,
    )


def _privacy_hash_ref(*, tenant_id: str, value: str) -> str:
    return hashlib.sha256(f"{tenant_id}:{value}".encode()).hexdigest()


def _add_deleted_count(target: dict[str, int], resource: str, count: int) -> None:
    if count > 0:
        target[resource] = count


def _contribution_summary(
    record: CabinetContributionRecord | None,
    *,
    member_id: str,
    period: str,
) -> CabinetContributionSummary:
    if record is None:
        return CabinetContributionSummary(
            member_id=member_id,
            period=period,
            total_points=0.0,
            avg_points_council=0.0,
            kv_raw=0.0,
            kv_capped=0.0,
            payout_share=0.0,
            contribution_count=0,
        )

    return CabinetContributionSummary(
        member_id=record.member_id,
        period=record.period,
        total_points=record.total_points,
        avg_points_council=record.avg_points_council,
        kv_raw=record.kv_raw,
        kv_capped=record.kv_capped,
        payout_share=record.payout_share,
        contribution_count=record.contribution_count,
    )


def _content_item(record: CabinetContentRecord) -> CabinetContentItem:
    return CabinetContentItem(
        content_id=record.content_id,
        template_id=record.template_id,
        title=record.title,
        preview=record.preview,
        content_hash=record.content_hash,
        platform_targets=record.platform_targets,
        points_awarded=record.points_awarded,
        created_at=record.created_at,
    )


def _onboarding_step_item(record: OnboardingStepRecord) -> OnboardingStepItem:
    return OnboardingStepItem(
        step_id=record.step_id,
        title=record.title,
        description=record.description,
        order=record.order,
        required=record.required,
        status=record.status,
        completed_at=record.completed_at,
    )


def _onboarding_consent_item(
    record: OnboardingConsentRecord,
) -> OnboardingConsentItem:
    return OnboardingConsentItem(
        key=record.key,
        label=record.label,
        required=record.required,
        granted=record.granted,
        granted_at=record.granted_at,
    )


def _onboarding_assistant_question_item(
    record: OnboardingAssistantAnswerRecord,
) -> OnboardingAssistantQuestionItem:
    return OnboardingAssistantQuestionItem(
        question_id=record.question_id,
        question=record.question,
        answer=record.answer,
        confidence=record.confidence,
        source_refs=record.source_refs,
        escalation_available=record.escalation_available,
    )


def _onboarding_assistant_answer_response(
    *,
    question: str,
    answer: OnboardingAssistantAnswerRecord | None,
) -> OnboardingAssistantAnswerResponse:
    if answer is None:
        return OnboardingAssistantAnswerResponse(
            matched_question_id="fallback",
            question=question,
            answer=(
                "Я не нашёл точный ответ в базе онбординга. Передайте вопрос "
                "Совету или куратору tenant для ручной проверки."
            ),
            confidence=0.0,
            source_refs=(),
            escalation_available=True,
            generated_at=datetime.now(UTC),
        )

    return OnboardingAssistantAnswerResponse(
        matched_question_id=answer.question_id,
        question=question,
        answer=answer.answer,
        confidence=answer.confidence,
        source_refs=answer.source_refs,
        escalation_available=answer.escalation_available,
        generated_at=datetime.now(UTC),
    )


def _onboarding_readiness(
    *,
    steps: tuple[OnboardingStepItem, ...],
    consents: tuple[OnboardingConsentItem, ...],
) -> OnboardingReadiness:
    required_steps = tuple(step for step in steps if step.required)
    completed_required_steps = sum(
        1 for step in required_steps if step.status == "completed"
    )
    required_consents = tuple(consent for consent in consents if consent.required)
    granted_required_consents = sum(
        1 for consent in required_consents if consent.granted
    )
    blockers: list[str] = []
    if completed_required_steps < len(required_steps):
        blockers.append("Есть незавершённые обязательные шаги")
    if granted_required_consents < len(required_consents):
        blockers.append("Не все обязательные согласия подтверждены")

    has_required_scope = len(required_steps) > 0 or len(required_consents) > 0
    ready_for_review = has_required_scope and not blockers
    return OnboardingReadiness(
        required_steps_total=len(required_steps),
        completed_required_steps=completed_required_steps,
        required_consents_total=len(required_consents),
        granted_required_consents=granted_required_consents,
        ready_for_review=ready_for_review,
        status="ready_for_review" if ready_for_review else "in_progress",
        blockers=tuple(blockers),
        recommendation=(
            "Передать анкету в Совет для проверки статуса"
            if ready_for_review
            else "Продолжить обязательные шаги онбординга"
        ),
    )


def _onboarding_progress_percent(readiness: OnboardingReadiness) -> int:
    total_required = readiness.required_steps_total + readiness.required_consents_total
    if total_required == 0:
        return 0

    completed_required = (
        readiness.completed_required_steps + readiness.granted_required_consents
    )
    return round(completed_required / total_required * 100)


def _wallet_balance_response(balance: WalletBalance) -> WalletBalanceResponse:
    return WalletBalanceResponse(
        tenant_id=balance.tenant_id,
        member_id=balance.member_id,
        balance_mcv=balance.balance_mcv,
        credited_mcv=balance.credited_mcv,
        debited_mcv=balance.debited_mcv,
        operation_count=balance.operation_count,
    )


def _wallet_operation_response(
    record: WalletOperationRecord,
) -> WalletOperationResponse:
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


def _dashboard_csv(overview: AnalyticsDashboardOverviewResponse) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("kind", "period", "category", "key", "label", "value", "status"))
    for metric in overview.metrics:
        writer.writerow(
            (
                "metric",
                overview.period,
                metric.category.value,
                metric.key,
                metric.label,
                _format_csv_number(metric.value),
                metric.status.value,
            )
        )
    for category in overview.categories:
        for key, value in category.totals.items():
            writer.writerow(
                (
                    "aggregate",
                    overview.period,
                    category.category.value,
                    key,
                    key,
                    _format_csv_number(value),
                    "",
                )
            )

    return output.getvalue()


def _dashboard_export_url(
    *,
    period: str,
    category: AnalyticsCategory | None,
) -> str:
    params = {"period": period}
    if category is not None:
        params["category"] = category.value

    return f"/analytics/dashboard/export?{urlencode(params)}"


def _dashboard_export_filename(
    *,
    period: str,
    category: AnalyticsCategory | None,
) -> str:
    suffix = category.value if category is not None else "all"
    return f"analytics-dashboard-{period}-{suffix}.csv"


def _render_voice_assistant_html(context: TenantContext) -> str:
    identity = f"{_escape(_subject(context))} · {_escape(context.tenant_id)}"
    tenant_id = _escape(context.tenant_id)
    correlation_id = _escape(_correlation_id(context))
    return _apply_design_system(f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>Голосовой ассистент</title>
  <style>
    :root {{
      --page: #f5f6f8;
      --panel: #ffffff;
      --ink: #17191f;
      --muted: #5d6675;
      --line: #d9dde5;
      --accent: #146c62;
      --accent-soft: #e6f1ee;
      --danger: #9f2f24;
      --danger-soft: #f8e8e5;
      --warning: #8a5a00;
      --warning-soft: #fff2cc;
      --info: #315078;
      --info-soft: #e8eef8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--page);
      color: var(--ink);
      font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
      line-height: 1.45;
    }}
    main {{
      width: min(1120px, calc(100% - 32px));
      margin: 0 auto;
      padding: 24px 0 36px;
    }}
    h1, h2, h3, p {{ margin: 0; }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: end;
      margin-bottom: 16px;
    }}
    h1 {{ font-size: 28px; line-height: 1.15; }}
    h2 {{ font-size: 17px; margin-bottom: 10px; }}
    h3 {{ font-size: 15px; margin-bottom: 4px; }}
    .muted {{ color: var(--muted); }}
    .status-line {{
      color: var(--muted);
      font-size: 14px;
      text-align: right;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }}
    .metric, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 1px 2px rgba(23, 25, 31, 0.04);
    }}
    .metric {{ min-height: 96px; padding: 12px; }}
    .metric-label {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 6px;
    }}
    .metric-value {{
      font-size: 22px;
      line-height: 1.15;
      font-weight: 700;
      overflow-wrap: anywhere;
    }}
    .metric-note {{
      color: var(--muted);
      font-size: 12px;
      margin-top: 8px;
    }}
    .assistant-shell {{
      display: grid;
      grid-template-columns: minmax(0, 0.9fr) minmax(360px, 1.1fr);
      gap: 14px;
      align-items: start;
    }}
    .stack {{ display: grid; gap: 14px; }}
    .panel {{ padding: 14px; }}
    .controls {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }}
    button {{
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--ink);
      padding: 0 14px;
      font-weight: 800;
      cursor: pointer;
    }}
    button:disabled {{
      cursor: not-allowed;
      opacity: 0.5;
    }}
    button.primary {{
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }}
    button.danger {{
      background: var(--danger);
      border-color: var(--danger);
      color: #fff;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      font-weight: 800;
      background: var(--info-soft);
      color: var(--info);
    }}
    .badge-ok {{
      background: var(--accent-soft);
      color: var(--accent);
    }}
    .badge-warn {{
      background: var(--warning-soft);
      color: var(--warning);
    }}
    .badge-error {{
      background: var(--danger-soft);
      color: var(--danger);
    }}
    audio {{
      width: 100%;
      min-height: 44px;
      margin-top: 12px;
    }}
    .result-grid {{
      display: grid;
      grid-template-columns: 150px minmax(0, 1fr);
      gap: 10px 12px;
      margin: 0;
    }}
    dt {{
      color: var(--muted);
      font-size: 13px;
    }}
    dd {{
      margin: 0;
      min-height: 20px;
      overflow-wrap: anywhere;
    }}
    .hash {{
      color: var(--muted);
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12px;
    }}
    .transcript {{
      min-height: 128px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
      padding: 12px;
      white-space: pre-wrap;
    }}
    @media (max-width: 760px) {{
      main {{ width: min(100% - 20px, 1120px); padding-top: 18px; }}
      header {{ display: grid; align-items: start; }}
      .status-line {{ text-align: left; }}
      .summary-grid, .assistant-shell, .result-grid {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body data-tenant-id="{tenant_id}" data-correlation-id="{correlation_id}">
  <main>
    <header>
      <div>
        <h1>Голосовой ассистент</h1>
        <p class="muted">{identity}</p>
      </div>
      <p class="status-line">Voice-to-Chain · voice.transcript.recorded</p>
    </header>
    <section class="summary-grid" aria-label="Сводка голосового ввода">
      <article class="metric">
        <p class="metric-label">Транскрипция</p>
        <p class="metric-value" data-summary="transcript">ожидает запись</p>
        <p class="metric-note">Whisper.cpp локально</p>
      </article>
      <article class="metric">
        <p class="metric-label">Hash audit</p>
        <p class="metric-value" data-summary="audit">не зафиксирован</p>
        <p class="metric-note">только SHA256 metadata</p>
      </article>
      <article class="metric">
        <p class="metric-label">Удаление аудио</p>
        <p class="metric-value" data-summary="retention">pending_deletion</p>
        <p class="metric-note">raw_audio_status</p>
      </article>
    </section>
    <section class="assistant-shell">
      <article class="panel">
        <h2>Запись</h2>
        <p class="badge" data-status>готов</p>
        <div class="controls">
          <button class="primary" type="button" data-record>Начать</button>
          <button class="danger" type="button" data-stop disabled>Стоп</button>
          <button type="button" data-send disabled>Отправить</button>
        </div>
        <audio controls data-audio></audio>
      </article>
      <article class="panel">
        <h2>Результат</h2>
        <p class="transcript" data-field="transcript"></p>
        <dl class="result-grid">
          <dt>transcript_sha256</dt>
          <dd class="hash" data-field="transcript_sha256"></dd>
          <dt>audit_hash</dt>
          <dd class="hash" data-field="audit_hash"></dd>
          <dt>block_ref</dt>
          <dd class="hash" data-field="block_ref"></dd>
          <dt>raw_audio_status</dt>
          <dd data-field="raw_audio_status"></dd>
          <dt>raw_audio_expires_at</dt>
          <dd data-field="raw_audio_expires_at"></dd>
        </dl>
      </article>
    </section>
  </main>
  <script>
    (() => {{
      const endpoint = "/voice-assistant/transcribe";
      const page = document.body.dataset;
      const status = document.querySelector("[data-status]");
      const recordButton = document.querySelector("[data-record]");
      const stopButton = document.querySelector("[data-stop]");
      const sendButton = document.querySelector("[data-send]");
      const audio = document.querySelector("[data-audio]");
      const summaries = {{
        transcript: document.querySelector('[data-summary="transcript"]'),
        audit: document.querySelector('[data-summary="audit"]'),
        retention: document.querySelector('[data-summary="retention"]'),
      }};
      const fields = {{
        transcript: document.querySelector('[data-field="transcript"]'),
        transcript_sha256: document.querySelector('[data-field="transcript_sha256"]'),
        audit_hash: document.querySelector('[data-field="audit_hash"]'),
        block_ref: document.querySelector('[data-field="block_ref"]'),
        raw_audio_status: document.querySelector('[data-field="raw_audio_status"]'),
        raw_audio_expires_at: document.querySelector(
          '[data-field="raw_audio_expires_at"]'
        ),
      }};
      const state = {{ recorder: null, chunks: [], stream: null, blob: null }};

      function setStatus(value, tone) {{
        status.textContent = value;
        status.className = "badge" + (tone ? " badge-" + tone : "");
      }}

      function authHeaders() {{
        const headers = {{
          "Content-Type": "application/json",
          "X-Tenant-Id": page.tenantId || "",
          "X-Correlation-Id": page.correlationId || "corr-voice-assistant-ui",
        }};
        const token = window.localStorage.getItem("media_center_jwt")
          || window.localStorage.getItem("mediaCenterToken");
        if (token) {{
          headers.Authorization = "Bearer " + token;
        }}
        return headers;
      }}

      function preferredMimeType() {{
        if (!window.MediaRecorder) {{
          return "";
        }}
        if (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")) {{
          return "audio/webm;codecs=opus";
        }}
        if (MediaRecorder.isTypeSupported("audio/webm")) {{
          return "audio/webm";
        }}
        return "";
      }}

      function blobToBase64(blob) {{
        return new Promise((resolve, reject) => {{
          const reader = new FileReader();
          reader.onloadend = () => {{
            const result = String(reader.result || "");
            resolve(result.includes(",") ? result.split(",")[1] : result);
          }};
          reader.onerror = () => reject(reader.error);
          reader.readAsDataURL(blob);
        }});
      }}

      async function startRecording() {{
        if (!navigator.mediaDevices || !window.MediaRecorder) {{
          setStatus("запись недоступна", "error");
          return;
        }}
        state.stream = await navigator.mediaDevices.getUserMedia({{ audio: true }});
        state.chunks = [];
        const mimeType = preferredMimeType();
        state.recorder = new MediaRecorder(
          state.stream,
          mimeType ? {{ mimeType }} : undefined,
        );
        state.recorder.addEventListener("dataavailable", (event) => {{
          if (event.data.size > 0) {{
            state.chunks.push(event.data);
          }}
        }});
        state.recorder.addEventListener("stop", () => {{
          const type = state.recorder.mimeType || "audio/webm";
          state.blob = new Blob(state.chunks, {{ type }});
          audio.src = URL.createObjectURL(state.blob);
          sendButton.disabled = false;
          recordButton.disabled = false;
          stopButton.disabled = true;
          if (state.stream) {{
            state.stream.getTracks().forEach((track) => track.stop());
          }}
          setStatus("готово к отправке", "warn");
        }});
        state.recorder.start();
        recordButton.disabled = true;
        stopButton.disabled = false;
        sendButton.disabled = true;
        setStatus("идёт запись", "warn");
      }}

      async function sendRecording() {{
        if (!state.blob) {{
          return;
        }}
        sendButton.disabled = true;
        setStatus("отправка", "warn");
        const audioBase64 = await blobToBase64(state.blob);
        const response = await fetch(endpoint, {{
          method: "POST",
          headers: authHeaders(),
          body: JSON.stringify({{
            content_type: state.blob.type || "audio/webm",
            audio_base64: audioBase64,
            captured_at: new Date().toISOString(),
          }}),
        }});
        const payload = await response.json();
        if (!response.ok) {{
          const message = payload.error ? payload.error.message : "ошибка";
          setStatus(message, "error");
          sendButton.disabled = false;
          return;
        }}
        fields.transcript.textContent = payload.transcript || "";
        fields.transcript_sha256.textContent = payload.transcript_sha256 || "";
        fields.audit_hash.textContent = payload.audit_hash || "";
        fields.block_ref.textContent = payload.block_ref || "";
        fields.raw_audio_status.textContent = payload.raw_audio_status || "";
        fields.raw_audio_expires_at.textContent = payload.raw_audio_expires_at || "";
        summaries.transcript.textContent = "готово";
        summaries.audit.textContent = "зафиксирован";
        summaries.retention.textContent =
          payload.raw_audio_status || "pending_deletion";
        setStatus("готово", "ok");
      }}

      recordButton.addEventListener("click", () => {{
        startRecording().catch((error) => setStatus(error.message, "error"));
      }});
      stopButton.addEventListener("click", () => {{
        if (state.recorder && state.recorder.state !== "inactive") {{
          state.recorder.stop();
        }}
      }});
      sendButton.addEventListener("click", () => {{
        sendRecording().catch((error) => {{
          setStatus(error.message, "error");
          sendButton.disabled = false;
        }});
      }});
    }})();
  </script>
</body>
</html>""")


def _render_analytics_dashboard_html(
    overview: AnalyticsDashboardOverviewResponse,
) -> str:
    identity = f"{_escape(overview.tenant_id)} · {_category_label(overview.category)}"
    status_line = (
        f"{_escape(overview.period)} · {_format_datetime(overview.generated_at)}"
    )
    metrics = _render_dashboard_metrics(overview.metrics)
    categories = _render_dashboard_categories(overview.categories)
    periods = _render_dashboard_periods(overview.period_slices)
    return _apply_design_system(f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>Дашборд KPI</title>
  <style>
    :root {{
      --page: #f5f6f8;
      --panel: #ffffff;
      --ink: #17191f;
      --muted: #5d6675;
      --line: #d9dde5;
      --accent: #146c62;
      --accent-soft: #e6f1ee;
      --danger: #9f2f24;
      --danger-soft: #f8e8e5;
      --warning: #8a5a00;
      --warning-soft: #fff2cc;
      --info: #315078;
      --info-soft: #e8eef8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--page);
      color: var(--ink);
      font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
      line-height: 1.45;
    }}
    main {{
      width: min(1240px, calc(100% - 32px));
      margin: 0 auto;
      padding: 24px 0 36px;
    }}
    h1, h2, h3, p {{ margin: 0; }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: end;
      margin-bottom: 16px;
    }}
    h1 {{ font-size: 28px; line-height: 1.15; }}
    h2 {{ font-size: 17px; margin-bottom: 10px; }}
    h3 {{ font-size: 15px; margin-bottom: 4px; }}
    .muted {{ color: var(--muted); }}
    .status-line {{
      color: var(--muted);
      font-size: 14px;
      text-align: right;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }}
    .metric, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 1px 2px rgba(23, 25, 31, 0.04);
    }}
    .metric {{ min-height: 112px; padding: 12px; }}
    .metric-label {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 6px;
    }}
    .metric-value {{
      font-size: 24px;
      line-height: 1.1;
      font-weight: 700;
      overflow-wrap: anywhere;
    }}
    .metric-note {{
      color: var(--muted);
      font-size: 12px;
      margin-top: 8px;
    }}
    .dashboard-shell {{
      display: grid;
      grid-template-columns: minmax(0, 1.35fr) minmax(320px, 0.65fr);
      gap: 14px;
      align-items: start;
    }}
    .stack {{ display: grid; gap: 14px; }}
    .panel {{ padding: 14px; }}
    .dashboard-section {{
      display: grid;
      gap: 10px;
      align-content: start;
    }}
    .dashboard-section h2 {{ margin-bottom: 0; }}
    .status {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      font-weight: 800;
      margin-top: 10px;
    }}
    .status-on_track {{
      background: var(--accent-soft);
      color: var(--accent);
    }}
    .status-below_target {{
      background: var(--danger-soft);
      color: var(--danger);
    }}
    .status-above_target {{
      background: var(--warning-soft);
      color: var(--warning);
    }}
    .export {{
      display: inline-flex;
      min-height: 36px;
      align-items: center;
      justify-content: center;
      border-radius: 8px;
      padding: 0 12px;
      background: var(--accent);
      color: #ffffff;
      text-decoration: none;
      font-weight: 800;
      margin-bottom: 12px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      padding: 10px 8px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    th {{ color: var(--muted); font-weight: 600; }}
    tr:last-child td {{ border-bottom: 0; }}
    .period-list {{
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 10px;
    }}
    .period-item {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
      padding: 12px;
    }}
    .period-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
      margin-top: 8px;
    }}
    .period-grid strong {{
      display: block;
      color: var(--ink);
      font-size: 16px;
    }}
    @media (max-width: 760px) {{
      main {{ width: min(100% - 20px, 1240px); padding-top: 18px; }}
      header {{ display: grid; align-items: start; }}
      .status-line {{ text-align: left; }}
      .summary-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .dashboard-shell, .period-grid {{ grid-template-columns: 1fr; }}
      th:nth-child(3), td:nth-child(3) {{ display: none; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Дашборд KPI</h1>
        <p class="muted">{identity}</p>
      </div>
      <p class="status-line">{status_line}</p>
    </header>
    <section class="summary-grid" aria-label="Сводка KPI">
      <article class="metric">
        <p class="metric-label">Всего KPI</p>
        <p class="metric-value">{overview.summary.metrics_total}</p>
        <p class="metric-note">показателей в срезе</p>
      </article>
      <article class="metric">
        <p class="metric-label">В норме</p>
        <p class="metric-value">{overview.summary.metrics_on_track}</p>
        <p class="metric-note">on track</p>
      </article>
      <article class="metric">
        <p class="metric-label">Ниже цели</p>
        <p class="metric-value">{overview.summary.metrics_below_target}</p>
        <p class="metric-note">below target</p>
      </article>
      <article class="metric">
        <p class="metric-label">Выше цели</p>
        <p class="metric-value">{overview.summary.metrics_above_target}</p>
        <p class="metric-note">above target</p>
      </article>
    </section>
    <section class="dashboard-shell">
      <div class="stack">
        <section class="dashboard-section">
          <h2>KPI</h2>
          <section class="summary-grid" aria-label="Метрики KPI">
            {metrics}
          </section>
        </section>
        <section class="dashboard-section">
          <h2>Категории</h2>
          {categories}
        </section>
      </div>
      <aside class="dashboard-section">
        <a class="export" href="{_escape(overview.export_url)}">Экспорт CSV</a>
        <h2>Периоды</h2>
        {periods}
      </aside>
    </section>
  </main>
</body>
</html>""")


def _render_dashboard_metrics(metrics: tuple[KPIMetric, ...]) -> str:
    if not metrics:
        return '<p class="muted">KPI нет</p>'

    cards = []
    for metric in metrics:
        cards.append(
            '<article class="metric">'
            f'<p class="metric-label">{_escape(_category_label(metric.category))}</p>'
            f'<p class="metric-value">{_format_float(metric.value)}</p>'
            f"<h3>{_escape(metric.label)}</h3>"
            f'<p class="metric-note">{_escape(_metric_target_label(metric))}</p>'
            f'<span class="status status-{_escape(metric.status.value)}">'
            f"{_escape(metric.status.value)}</span>"
            "</article>"
        )

    return "".join(cards)


def _render_dashboard_categories(
    categories: tuple[AnalyticsCategoryAggregate, ...],
) -> str:
    rows = []
    for category in categories:
        totals = ", ".join(
            f"{_escape(key)}: {_format_float(value)}"
            for key, value in category.totals.items()
        )
        rows.append(
            "<tr>"
            f"<td>{_escape(_category_label(category.category))}</td>"
            f"<td>{category.event_count}</td>"
            f"<td>{category.unique_members}</td>"
            f"<td>{totals or '0'}</td>"
            "</tr>"
        )

    return (
        "<table>"
        "<thead><tr><th>Категория</th><th>События</th><th>Участники</th>"
        "<th>Агрегаты</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _render_dashboard_periods(
    periods: tuple[AnalyticsDashboardPeriodSlice, ...],
) -> str:
    if not periods:
        return '<p class="muted">Периодов нет</p>'

    items = []
    for period in periods:
        items.append(
            '<li class="period-item">'
            f"<h3>{_escape(period.period)}</h3>"
            '<div class="period-grid">'
            f"<p>События<strong>{period.event_count}</strong></p>"
            f"<p>Участники<strong>{period.unique_members}</strong></p>"
            f"<p>В норме<strong>{period.metrics_on_track}</strong></p>"
            "</div>"
            "</li>"
        )

    return f'<ul class="period-list">{"".join(items)}</ul>'


def _render_onboarding_html(overview: OnboardingOverviewResponse) -> str:
    identity = f"{_escape(overview.member_id)} · {_escape(overview.tenant_id)}"
    status_line = (
        f"до {_escape(_format_datetime(overview.target_finish_at))} · "
        f"{overview.target_window_hours} ч"
    )
    readiness_label = (
        "Готов к проверке"
        if overview.readiness.ready_for_review
        else "Онбординг продолжается"
    )
    steps = _render_onboarding_steps(overview.steps)
    consents = _render_onboarding_consents(overview.consents)
    assistant = _render_onboarding_assistant(overview.assistant)
    blockers = _render_onboarding_blockers(overview.readiness.blockers)
    steps_ratio = (
        f"{overview.readiness.completed_required_steps}/"
        f"{overview.readiness.required_steps_total}"
    )
    consents_ratio = (
        f"{overview.readiness.granted_required_consents}/"
        f"{overview.readiness.required_consents_total}"
    )
    return _apply_design_system(f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>Онбординг участника</title>
  <style>
    :root {{
      --page: #f5f6f8;
      --panel: #ffffff;
      --ink: #17191f;
      --muted: #5d6675;
      --line: #d9dde5;
      --accent: #146c62;
      --accent-soft: #e6f1ee;
      --warning: #8a5a00;
      --warning-soft: #fff2cc;
      --info: #315078;
      --info-soft: #e8eef8;
      --danger: #9f2f24;
      --danger-soft: #f8e8e5;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--page);
      color: var(--ink);
      font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
      line-height: 1.45;
    }}
    main {{
      width: min(1240px, calc(100% - 32px));
      margin: 0 auto;
      padding: 24px 0 36px;
    }}
    h1, h2, h3, p {{ margin: 0; }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: end;
      margin-bottom: 16px;
    }}
    h1 {{ font-size: 28px; line-height: 1.15; }}
    h2 {{ font-size: 17px; margin-bottom: 10px; }}
    h3 {{ font-size: 15px; margin-bottom: 4px; }}
    .muted {{ color: var(--muted); }}
    .status-line {{
      color: var(--muted);
      font-size: 14px;
      text-align: right;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }}
    .metric, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 1px 2px rgba(23, 25, 31, 0.04);
    }}
    .metric {{ min-height: 106px; padding: 12px; }}
    .metric-label {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 6px;
    }}
    .metric-value {{
      font-size: 24px;
      line-height: 1.1;
      font-weight: 700;
      overflow-wrap: anywhere;
    }}
    .metric-note {{
      color: var(--muted);
      font-size: 12px;
      margin-top: 8px;
    }}
    .onboarding-shell {{
      display: grid;
      grid-template-columns: minmax(0, 1.25fr) minmax(320px, 0.75fr);
      gap: 14px;
      align-items: start;
    }}
    .stack {{ display: grid; gap: 14px; }}
    .panel {{ padding: 14px; }}
    .step-list, .consent-list, .assistant-list, .blocker-list {{
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 10px;
    }}
    .step-item, .consent-item, .assistant-item, .blocker-item {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
      padding: 12px;
    }}
    .item-top {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      margin-bottom: 6px;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      font-weight: 800;
      white-space: nowrap;
    }}
    .badge-completed, .badge-granted, .badge-ready_for_review {{
      background: var(--accent-soft);
      color: var(--accent);
    }}
    .badge-available, .badge-in_progress {{
      background: var(--info-soft);
      color: var(--info);
    }}
    .badge-blocked, .badge-revoked {{
      background: var(--danger-soft);
      color: var(--danger);
    }}
    .badge-required {{
      background: var(--warning-soft);
      color: var(--warning);
    }}
    .progress-track {{
      width: 100%;
      height: 10px;
      border-radius: 999px;
      background: #e3e7ee;
      overflow: hidden;
      margin-top: 10px;
    }}
    .progress-fill {{
      height: 100%;
      width: {overview.progress_percent}%;
      background: var(--accent);
    }}
    .assistant-answer {{
      color: var(--muted);
      margin-top: 6px;
    }}
    .source-list {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 10px;
    }}
    .source {{
      background: var(--info-soft);
      color: var(--info);
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      font-weight: 700;
    }}
    @media (max-width: 760px) {{
      main {{ width: min(100% - 20px, 1240px); padding-top: 18px; }}
      header {{ display: grid; align-items: start; }}
      .status-line {{ text-align: left; }}
      .summary-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .onboarding-shell {{ grid-template-columns: 1fr; }}
      .item-top {{ align-items: start; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Онбординг участника</h1>
        <p class="muted">{identity}</p>
      </div>
      <p class="status-line">{status_line}</p>
    </header>
    <section class="summary-grid" aria-label="Сводка онбординга">
      <article class="metric">
        <p class="metric-label">Прогресс</p>
        <p class="metric-value">{overview.progress_percent}%</p>
        <div class="progress-track" aria-hidden="true">
          <div class="progress-fill"></div>
        </div>
      </article>
      <article class="metric">
        <p class="metric-label">Готовность</p>
        <p class="metric-value">{readiness_label}</p>
        <p class="metric-note">{_escape(overview.readiness.recommendation)}</p>
      </article>
      <article class="metric">
        <p class="metric-label">Шаги</p>
        <p class="metric-value">{steps_ratio}</p>
        <p class="metric-note">обязательных завершено</p>
      </article>
      <article class="metric">
        <p class="metric-label">Согласия</p>
        <p class="metric-value">{consents_ratio}</p>
        <p class="metric-note">обязательных подтверждено</p>
      </article>
    </section>
    <section class="onboarding-shell">
      <div class="stack">
        <article class="panel">
          <h2>Шаги</h2>
          {steps}
        </article>
        <article class="panel">
          <h2>Согласия</h2>
          {consents}
        </article>
      </div>
      <aside class="stack">
        <article class="panel">
          <h2>AI-ассистент</h2>
          {assistant}
        </article>
        <article class="panel">
          <h2>Проверка готовности</h2>
          <p class="muted">{_escape(overview.readiness.recommendation)}</p>
          {blockers}
        </article>
      </aside>
    </section>
  </main>
</body>
</html>""")


def _render_onboarding_steps(steps: tuple[OnboardingStepItem, ...]) -> str:
    if not steps:
        return '<p class="muted">Шаги онбординга ещё не назначены</p>'

    items = []
    for step in steps:
        requirement = "обязательный" if step.required else "дополнительный"
        items.append(
            '<li class="step-item">'
            '<div class="item-top">'
            f"<h3>{_escape(step.title)}</h3>"
            f'<span class="badge badge-{_escape(step.status)}">'
            f"{_escape(_onboarding_status_label(step.status))}</span>"
            "</div>"
            f'<p class="muted">{_escape(step.description)}</p>'
            f'<p class="metric-note">{_escape(requirement)} · шаг {step.order}</p>'
            "</li>"
        )

    return f'<ul class="step-list">{"".join(items)}</ul>'


def _render_onboarding_consents(
    consents: tuple[OnboardingConsentItem, ...],
) -> str:
    if not consents:
        return '<p class="muted">Согласия ещё не запрошены</p>'

    items = []
    for consent in consents:
        status = "granted" if consent.granted else "revoked"
        status_label = "подтверждено" if consent.granted else "отозвано"
        requirement = "обязательное" if consent.required else "опциональное"
        note = f"{_escape(requirement)} · {_escape(consent.key)}"
        items.append(
            '<li class="consent-item">'
            '<div class="item-top">'
            f"<h3>{_escape(consent.label)}</h3>"
            f'<span class="badge badge-{status}">{_escape(status_label)}</span>'
            "</div>"
            f'<p class="metric-note">{note}</p>'
            "</li>"
        )

    return f'<ul class="consent-list">{"".join(items)}</ul>'


def _render_onboarding_assistant(summary: OnboardingAssistantSummary) -> str:
    if not summary.suggested_questions:
        return '<p class="muted">AI-ассистент ожидает базу типовых вопросов</p>'

    items = []
    for item in summary.suggested_questions:
        confidence = _format_float(item.confidence)
        sources = "".join(
            f'<span class="source">{_escape(source)}</span>'
            for source in item.source_refs
        )
        items.append(
            '<li class="assistant-item">'
            '<div class="item-top">'
            f"<h3>{_escape(item.question)}</h3>"
            f'<span class="badge badge-available">{confidence}</span>'
            "</div>"
            f'<p class="assistant-answer">{_escape(item.answer)}</p>'
            f'<div class="source-list">{sources}</div>'
            "</li>"
        )

    return f'<ul class="assistant-list">{"".join(items)}</ul>'


def _render_onboarding_blockers(blockers: tuple[str, ...]) -> str:
    if not blockers:
        return '<p class="metric-note">Блокеров нет</p>'

    items = [
        f'<li class="blocker-item">{_escape(blocker)}</li>' for blocker in blockers
    ]
    return f'<ul class="blocker-list">{"".join(items)}</ul>'


def _render_cabinet_html(overview: WebCabinetOverviewResponse) -> str:
    operations = _render_operations(overview.operations)
    content = _render_content(overview.content)
    links = _render_links(overview.referral_links)
    identity = f"{_escape(overview.member_id)} · {_escape(overview.tenant_id)}"
    balance_mcv = _format_mcv(overview.balance.balance_mcv)
    credited_mcv = _format_mcv(overview.balance.credited_mcv)
    debited_mcv = _format_mcv(overview.balance.debited_mcv)
    avg_points = _format_float(overview.contribution.avg_points_council)
    return _apply_design_system(
        f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>Кабинет пайщика</title>
  <style>
    :root {{
      --page: #f6f7f9;
      --panel: #ffffff;
      --ink: #17191f;
      --muted: #5d6675;
      --line: #d9dde5;
      --accent: #146c62;
      --accent-soft: #e6f1ee;
      --signal: #8a5a00;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--page);
      color: var(--ink);
      font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
      line-height: 1.5;
    }}
    main {{
      width: min(1180px, calc(100% - 32px));
      margin: 0 auto;
      padding: 28px 0 40px;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: end;
      margin-bottom: 18px;
    }}
    h1, h2, h3, p {{ margin: 0; }}
    h1 {{ font-size: 28px; line-height: 1.15; font-weight: 700; }}
    h2 {{ font-size: 18px; margin-bottom: 12px; }}
    h3 {{ font-size: 15px; margin-bottom: 4px; }}
    .muted {{ color: var(--muted); }}
    .period {{
      color: var(--muted);
      font-size: 14px;
      text-align: right;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }}
    .metric, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 1px 2px rgba(23, 25, 31, 0.04);
    }}
    .metric {{ padding: 14px; min-height: 104px; }}
    .metric-label {{
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 8px;
    }}
    .metric-value {{
      font-size: 24px;
      line-height: 1.15;
      font-weight: 700;
      overflow-wrap: anywhere;
    }}
    .metric-note {{
      color: var(--muted);
      font-size: 13px;
      margin-top: 8px;
    }}
    .cabinet-shell {{
      display: grid;
      grid-template-columns: minmax(0, 1.25fr) minmax(300px, 0.75fr);
      gap: 16px;
      align-items: start;
    }}
    .stack {{ display: grid; gap: 16px; }}
    .panel {{ padding: 16px; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      padding: 10px 8px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    th {{ color: var(--muted); font-weight: 600; }}
    tr:last-child td {{ border-bottom: 0; }}
    .amount-positive {{ color: var(--accent); font-weight: 700; }}
    .amount-negative {{ color: var(--signal); font-weight: 700; }}
    .content-list, .link-list {{
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 12px;
    }}
    .content-item, .link-item {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fbfcfd;
    }}
    .tags {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 10px;
    }}
    .tag {{
      background: var(--accent-soft);
      color: var(--accent);
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      font-weight: 700;
    }}
    .link-url {{
      color: var(--accent);
      overflow-wrap: anywhere;
      font-size: 14px;
    }}
    @media (max-width: 720px) {{
      main {{ width: min(100% - 20px, 1180px); padding-top: 18px; }}
      header {{ display: grid; align-items: start; }}
      .period {{ text-align: left; }}
      .summary-grid, .cabinet-shell {{ grid-template-columns: 1fr; }}
      .metric-value {{ font-size: 22px; }}
      th:nth-child(3), td:nth-child(3) {{ display: none; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Личный кабинет пайщика</h1>
        <p class="muted">{identity}</p>
      </div>
      <p class="period">{_escape(overview.period)}</p>
    </header>
    <section class="summary-grid" aria-label="Сводка">
      <article class="metric">
        <p class="metric-label">Баланс</p>
        <p class="metric-value">{balance_mcv} МСЦ</p>
        <p class="metric-note">+{credited_mcv} / -{debited_mcv}</p>
      </article>
      <article class="metric">
        <p class="metric-label">Вклад</p>
        <p class="metric-value">{_format_float(overview.contribution.total_points)}</p>
        <p class="metric-note">{overview.contribution.contribution_count} событий</p>
      </article>
      <article class="metric">
        <p class="metric-label">Кв</p>
        <p class="metric-value">{_format_share(overview.contribution.kv_capped)}</p>
        <p class="metric-note">raw {_format_share(overview.contribution.kv_raw)}</p>
      </article>
      <article class="metric">
        <p class="metric-label">Доля</p>
        <p class="metric-value">{_format_share(overview.contribution.payout_share)}</p>
        <p class="metric-note">среднее Совета {avg_points}</p>
      </article>
    </section>
    <section class="cabinet-shell">
      <div class="stack">
        <article class="panel">
          <h2>История операций</h2>
          {operations}
        </article>
        <article class="panel">
          <h2>Контент</h2>
          {content}
        </article>
      </div>
      <aside class="stack">
        <article class="panel">
          <h2>Реферальные ссылки</h2>
          {links}
        </article>
      </aside>
    </section>
  </main>
</body>
</html>""",
        mobile_breakpoint_px=720,
    )


def _render_council_panel_html(overview: CouncilPanelOverviewResponse) -> str:
    identity = f"{_escape(overview.tenant_id)} · роль {_escape(overview.role)}"
    two_factor_label = "2FA active" if overview.two_factor.active else "2FA required"
    status_line = f"{two_factor_label} · {_format_datetime(overview.generated_at)}"
    queue = _render_council_queue(overview.payouts)
    details = _render_council_details(overview.payouts[0] if overview.payouts else None)
    policies = _render_council_policies(overview.policies)
    return _apply_design_system(f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>Панель Совета</title>
  <style>
    :root {{
      --page: #f5f6f8;
      --panel: #ffffff;
      --ink: #17191f;
      --muted: #5d6675;
      --line: #d9dde5;
      --accent: #146c62;
      --accent-soft: #e6f1ee;
      --danger: #9f2f24;
      --danger-soft: #f8e8e5;
      --warning: #8a5a00;
      --warning-soft: #fff2cc;
      --info-soft: #e8eef8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--page);
      color: var(--ink);
      font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
      line-height: 1.45;
    }}
    main {{
      width: min(1240px, calc(100% - 32px));
      margin: 0 auto;
      padding: 24px 0 36px;
    }}
    h1, h2, h3, p {{ margin: 0; }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: end;
      margin-bottom: 16px;
    }}
    h1 {{ font-size: 28px; line-height: 1.15; }}
    h2 {{ font-size: 17px; margin-bottom: 10px; }}
    h3 {{ font-size: 15px; margin-bottom: 4px; }}
    .muted {{ color: var(--muted); }}
    .status-line {{
      color: var(--muted);
      font-size: 14px;
      text-align: right;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }}
    .metric, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 1px 2px rgba(23, 25, 31, 0.04);
    }}
    .metric {{ min-height: 86px; padding: 12px; }}
    .metric-label {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 6px;
    }}
    .metric-value {{
      font-size: 24px;
      line-height: 1.1;
      font-weight: 700;
    }}
    .panel-shell {{
      display: grid;
      grid-template-columns: 270px minmax(0, 1fr) minmax(320px, 0.9fr);
      gap: 14px;
      align-items: start;
    }}
    .panel {{ padding: 14px; }}
    nav ul, .queue-list, .audit-list, .policy-list {{
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 10px;
    }}
    nav li {{
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      color: var(--muted);
      font-weight: 700;
    }}
    nav li:first-child {{
      color: var(--accent);
      background: var(--accent-soft);
      border-color: #bad9d3;
    }}
    .queue-item, .policy-item, .audit-item {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
      padding: 12px;
    }}
    .queue-top {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      margin-bottom: 8px;
    }}
    .risk {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      font-weight: 800;
    }}
    .risk-high, .risk-critical {{
      background: var(--danger-soft);
      color: var(--danger);
    }}
    .risk-medium {{
      background: var(--warning-soft);
      color: var(--warning);
    }}
    .risk-low {{
      background: var(--info-soft);
      color: #315078;
    }}
    .queue-meta, .detail-grid {{
      display: grid;
      gap: 6px;
      color: var(--muted);
      font-size: 13px;
    }}
    .detail-grid {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
      margin: 12px 0;
    }}
    .detail-grid strong {{
      display: block;
      color: var(--ink);
      overflow-wrap: anywhere;
    }}
    .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }}
    button {{
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--ink);
      padding: 0 12px;
      font-weight: 800;
    }}
    button.danger {{
      background: var(--danger);
      border-color: var(--danger);
      color: #fff;
    }}
    button.primary {{
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }}
    .hash {{
      color: var(--muted);
      overflow-wrap: anywhere;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12px;
    }}
    @media (max-width: 760px) {{
      main {{ width: min(100% - 20px, 1240px); padding-top: 18px; }}
      header {{ display: grid; align-items: start; }}
      .status-line {{ text-align: left; }}
      .summary-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .panel-shell, .detail-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Панель Совета</h1>
        <p class="muted">{identity}</p>
      </div>
      <p class="status-line">{status_line}</p>
    </header>
    <section class="summary-grid" aria-label="Сводка HITL">
      <article class="metric">
        <p class="metric-label">В очереди</p>
        <p class="metric-value">{overview.summary.queued}</p>
      </article>
      <article class="metric">
        <p class="metric-label">Истекает</p>
        <p class="metric-value">{overview.summary.veto_window_expiring}</p>
      </article>
      <article class="metric">
        <p class="metric-label">2FA</p>
        <p class="metric-value">{overview.summary.requires_2fa}</p>
      </article>
      <article class="metric">
        <p class="metric-label">Вето</p>
        <p class="metric-value">{overview.summary.canceled}</p>
      </article>
      <article class="metric">
        <p class="metric-label">Исполнено</p>
        <p class="metric-value">{overview.summary.executed}</p>
      </article>
    </section>
    <section class="panel-shell">
      <aside class="panel">
        <h2>Разделы</h2>
        <nav aria-label="Разделы панели Совета">
          <ul>
            <li>Очередь HITL</li>
            <li>Вето</li>
            <li>Политики</li>
            <li>Аудит</li>
            <li>KPI</li>
          </ul>
        </nav>
      </aside>
      <article class="panel">
        <h2>Очередь выплат</h2>
        {queue}
      </article>
      <aside class="panel">
        <h2>Детали операции</h2>
        {details}
        <h2>Политики</h2>
        {policies}
      </aside>
    </section>
  </main>
</body>
</html>""")


def _render_council_queue(payouts: tuple[CouncilPanelPayoutItem, ...]) -> str:
    if not payouts:
        return '<p class="muted">Очередь HITL пуста</p>'

    items = []
    for payout in payouts:
        items.append(
            '<li class="queue-item">'
            '<div class="queue-top">'
            f"<h3>{_escape(payout.payout_id)}</h3>"
            f'<span class="risk risk-{_escape(payout.risk_level)}">'
            f"{_escape(payout.risk_level.upper())}</span>"
            "</div>"
            '<div class="queue-meta">'
            f"<span>Окно вето до {_escape(_format_datetime(payout.veto_until))}</span>"
            f"<span>Участник {_escape(payout.member_id)} · "
            f"доля {_format_share(payout.payout_share)}</span>"
            f"<span>{_escape(payout.risk_reason)}</span>"
            "</div>"
            '<div class="actions">'
            '<button class="danger" type="button">Вето</button>'
            '<button class="primary" type="button">Подтвердить</button>'
            "</div>"
            "</li>"
        )

    return f'<ul class="queue-list">{"".join(items)}</ul>'


def _render_council_details(payout: CouncilPanelPayoutItem | None) -> str:
    if payout is None:
        return '<p class="muted">Выберите выплату из очереди</p>'

    audit = _render_council_audit(payout.audit_timeline)
    policy_label = f"{_escape(payout.policy_key)} v{payout.policy_version}"
    return (
        '<div class="detail-grid">'
        f"<p>Период<strong>{_escape(payout.period)}</strong></p>"
        f"<p>Статус<strong>{_escape(payout.status.value)}</strong></p>"
        f"<p>Policy<strong>{policy_label}</strong></p>"
        f"<p>Доля<strong>{_format_share(payout.payout_share)}</strong></p>"
        f"<p>Источник<strong>{_escape(payout.calculation.source)}</strong></p>"
        f"<p>2FA<strong>{'нужно' if payout.requires_2fa else 'не нужно'}</strong></p>"
        "</div>"
        f'<p class="muted">{_escape(payout.calculation.explanation)}</p>'
        f'<p class="hash">{_escape(payout.calculation.distribution_hash)}</p>'
        '<div class="actions">'
        '<button class="danger" type="button">Вето</button>'
        '<button class="primary" type="button">Подтвердить</button>'
        "</div>"
        "<h2>Audit timeline</h2>"
        f"{audit}"
    )


def _render_council_audit(
    records: tuple[CouncilPanelAuditItem, ...],
) -> str:
    if not records:
        return '<p class="muted">Audit timeline пуст</p>'

    items = []
    for record in records:
        items.append(
            '<li class="audit-item">'
            f"<h3>{_escape(record.event_type)}</h3>"
            f'<p class="muted">{_escape(record.event_id)} · '
            f"{_escape(_format_datetime(record.occurred_at))}</p>"
            f'<p class="hash">{_escape(record.audit_hash)}</p>'
            "</li>"
        )

    return f'<ul class="audit-list">{"".join(items)}</ul>'


def _render_council_policies(policies: tuple[PolicyRecord, ...]) -> str:
    if not policies:
        return '<p class="muted">Политик нет</p>'

    items = []
    for policy in policies:
        items.append(
            '<li class="policy-item">'
            f"<h3>{_escape(policy.key)} v{policy.version}</h3>"
            f'<p class="muted">Обновил {_escape(policy.updated_by or "default")}</p>'
            f'<p class="hash">{_escape(policy.audit_hash)}</p>'
            "</li>"
        )

    return f'<ul class="policy-list">{"".join(items)}</ul>'


def _render_operations(operations: tuple[WalletOperationResponse, ...]) -> str:
    if not operations:
        return '<p class="muted">Операций нет</p>'

    rows = []
    for operation in operations:
        amount_class = (
            "amount-positive"
            if operation.amount_mcv >= Decimal("0")
            else "amount-negative"
        )
        amount = _format_signed_mcv(operation.amount_mcv)
        rows.append(
            "<tr>"
            f"<td>{_escape(_operation_type_label(operation.type))}</td>"
            f'<td class="{amount_class}">{amount}</td>'
            f"<td>{_escape(operation.ref_type)} · {_escape(operation.ref_id)}</td>"
            f"<td>{_escape(_format_datetime(operation.created_at))}</td>"
            "</tr>"
        )

    return (
        "<table>"
        "<thead><tr><th>Тип</th><th>Сумма</th><th>Связь</th><th>Дата</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _render_content(content: tuple[CabinetContentItem, ...]) -> str:
    if not content:
        return '<p class="muted">Контента нет</p>'

    items = []
    for item in content:
        tags = "".join(
            f'<span class="tag">{_escape(target)}</span>'
            for target in item.platform_targets
        )
        items.append(
            '<li class="content-item">'
            f"<h3>{_escape(item.title)}</h3>"
            f'<p class="muted">{_escape(item.preview)}</p>'
            f'<p class="metric-note">{_format_float(item.points_awarded)} баллов · '
            f"{_escape(_format_datetime(item.created_at))}</p>"
            f'<div class="tags">{tags}</div>'
            "</li>"
        )

    return f'<ul class="content-list">{"".join(items)}</ul>'


def _render_links(links: tuple[CabinetReferralLink, ...]) -> str:
    if not links:
        return '<p class="muted">Ссылок нет</p>'

    items = []
    for link in links:
        items.append(
            '<li class="link-item">'
            f"<h3>{_escape(link.level)} · {_escape(link.owner_id)}</h3>"
            f'<p class="link-url">{_escape(link.url)}</p>'
            f'<p class="metric-note">Доля {_format_share(link.reward_share)}</p>'
            "</li>"
        )

    return f'<ul class="link-list">{"".join(items)}</ul>'


def _apply_design_system(
    html_document: str,
    *,
    mobile_breakpoint_px: int = 760,
) -> str:
    return (
        html_document.replace(
            "  <style>\n",
            (
                "  <style>\n"
                f"    {design_system_css(mobile_breakpoint_px=mobile_breakpoint_px)}\n"
            ),
            1,
        )
        .replace(
            "<body",
            f'<body data-design-system="{DESIGN_SYSTEM_NAME}"',
            1,
        )
        .replace(
            "<main>",
            '<main class="mc-app-shell" data-component="AppShell">',
            1,
        )
        .replace(
            '<section class="summary-grid"',
            '<section class="summary-grid mc-summary-grid"',
        )
        .replace(
            '<section class="dashboard-section"',
            '<section class="dashboard-section mc-panel" data-component="Panel"',
        )
        .replace(
            '<aside class="dashboard-section"',
            '<aside class="dashboard-section mc-panel" data-component="Panel"',
        )
        .replace(
            '<article class="metric">',
            '<article class="metric mc-metric" data-component="MetricTile">',
        )
        .replace(
            '<article class="panel">',
            '<article class="panel mc-panel" data-component="Panel">',
        )
        .replace(
            '<aside class="panel">',
            '<aside class="panel mc-panel" data-component="Panel">',
        )
    )


def _operation_type_label(operation_type: WalletOperationType) -> str:
    labels = {
        WalletOperationType.DISTRIBUTION_CREDIT: "Начисление",
        WalletOperationType.PAYOUT_DEBIT: "Списание",
        WalletOperationType.MANUAL_ADJUSTMENT: "Корректировка",
    }
    return labels[operation_type]


def _onboarding_status_label(status: str) -> str:
    labels = {
        "available": "доступно",
        "in_progress": "в работе",
        "completed": "готово",
        "blocked": "блокер",
    }
    return labels.get(status, status)


def _category_label(category: AnalyticsCategory | None) -> str:
    labels = {
        AnalyticsCategory.PARTICIPATION: "Участие",
        AnalyticsCategory.CONTENT: "Контент",
        AnalyticsCategory.ENGAGEMENT: "Вовлечённость",
        AnalyticsCategory.ACTIONS: "Действия",
    }
    if category is None:
        return "Все категории"

    return labels[category]


def _metric_target_label(metric: KPIMetric) -> str:
    if metric.target_min is None and metric.target_max is None:
        return metric.target_window
    if metric.target_min is None:
        return f"цель до {_format_float(metric.target_max or 0)}"
    if metric.target_max is None:
        return f"цель от {_format_float(metric.target_min)}"

    return f"цель {_format_float(metric.target_min)}-{_format_float(metric.target_max)}"


def _format_mcv(value: Decimal) -> str:
    return f"{value:.2f}"


def _format_signed_mcv(value: Decimal) -> str:
    sign = "+" if value >= Decimal("0") else ""
    return f"{sign}{_format_mcv(value)}"


def _format_float(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _format_share(value: float) -> str:
    return f"{value * 100:.1f}%"


def _format_csv_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))

    return _format_float(value)


def _normalize_onboarding_question(value: str) -> str:
    lowered = value.casefold()
    punctuation = "?!.,:;()[]{}«»\"'"
    for mark in punctuation:
        lowered = lowered.replace(mark, " ")

    return " ".join(lowered.split())


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC")


def _escape(value: str) -> str:
    return html.escape(value, quote=True)


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


async def _payout_not_found_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content=error_response_body(
            code="payout_not_found",
            message=str(exc),
        ),
    )


async def _veto_window_closed_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content=error_response_body(
            code="veto_window_closed",
            message=str(exc),
        ),
    )


async def _payout_not_executable_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content=error_response_body(
            code="payout_not_executable",
            message=str(exc),
        ),
    )


async def _payout_queue_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=error_response_body(
            code="hitl_payout_error",
            message=str(exc),
        ),
    )


async def _policy_not_found_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content=error_response_body(
            code="policy_not_found",
            message=str(exc),
        ),
    )


async def _policy_manager_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=error_response_body(
            code="policy_manager_error",
            message=str(exc),
        ),
    )


async def _voice_to_chain_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=error_response_body(
            code="voice_to_chain_error",
            message=str(exc),
        ),
    )


async def _audio_retention_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content=error_response_body(
            code="voice_audio_retention_conflict",
            message=str(exc),
        ),
    )


async def _whisper_cpp_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content=error_response_body(
            code="voice_transcription_failed",
            message=str(exc),
        ),
    )


async def _audit_metadata_policy_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=error_response_body(
            code="audit_metadata_policy_violation",
            message=str(exc),
        ),
    )


async def _audit_batch_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=error_response_body(
            code="audit_batch_invalid",
            message=str(exc),
        ),
    )


async def _audit_record_conflict_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content=error_response_body(
            code="audit_record_conflict",
            message=str(exc),
        ),
    )
