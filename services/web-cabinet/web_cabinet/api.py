from __future__ import annotations

import html
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, cast

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from hitl_payout_gateway import (
    PAYOUT_CONFIRM_OPERATION,
    ConfirmPayoutRequest,
    InMemoryPayoutQueueRepository,
    PayoutConfirmation,
    PayoutConfirmationManager,
    PayoutNotExecutableError,
    PayoutNotFoundError,
    PayoutQueueError,
    PayoutQueueItem,
    PayoutQueueManager,
    PayoutStatus,
    VetoDecision,
    VetoManager,
    VetoPayoutRequest,
    VetoWindowClosedError,
)
from policy_manager import (
    InMemoryPolicyRepository,
    PolicyManager,
    PolicyManagerError,
    PolicyNotFoundError,
    PolicyRecord,
    PolicyUpdateInput,
    UpdatePolicyRequest,
)
from pydantic import Field
from wallet import (
    InMemoryWalletRepository,
    WalletBalance,
    WalletBalanceResponse,
    WalletOperationRecord,
    WalletOperationResponse,
    WalletOperationType,
)

from libs.shared import (
    BOARD_ROLE,
    COUNCIL_ROLE,
    MEMBER_ASSOC_ROLE,
    MEMBER_FULL_ROLE,
    PRESIDIUM_ROLE,
    VALIDATION_ERROR_CODE,
    AccessPolicy,
    InMemoryAuditSink,
    ServiceTemplateConfig,
    SharedBaseModel,
    SharedError,
    SubjectId,
    TenantContext,
    TenantCoreError,
    TenantId,
    TenantScopedRepository,
    TOTPService,
    create_service_app,
    error_response_body,
    require_access,
    require_tenant_context,
)

WEB_CABINET_SERVICE_NAME = "web-cabinet"

_PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
_REFERRAL_LEVEL_PATTERN = r"^L[1-3]$"
_PLATFORM_TARGET_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"

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
    _contribution_guard: TenantScopedRepository[CabinetContributionRecord] = field(
        default_factory=lambda: TenantScopedRepository("cabinet_contributions")
    )
    _content_guard: TenantScopedRepository[CabinetContentRecord] = field(
        default_factory=lambda: TenantScopedRepository("cabinet_content")
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
    wallet_repository: InMemoryWalletRepository
    tenant_audit_sink: InMemoryAuditSink
    payout_queue_manager: PayoutQueueManager
    veto_manager: VetoManager
    confirmation_manager: PayoutConfirmationManager
    policy_manager: PolicyManager
    council_panel_repository: InMemoryCouncilPanelRepository
    totp_secrets: dict[tuple[str, str], str]


router = APIRouter(tags=["Web Cabinet"])


def create_web_cabinet_app(
    config: ServiceTemplateConfig,
    *,
    repository: InMemoryWebCabinetRepository | None = None,
    wallet_repository: InMemoryWalletRepository | None = None,
    tenant_audit_sink: InMemoryAuditSink | None = None,
    payout_queue_manager: PayoutQueueManager | None = None,
    veto_manager: VetoManager | None = None,
    confirmation_manager: PayoutConfirmationManager | None = None,
    policy_manager: PolicyManager | None = None,
    council_panel_repository: InMemoryCouncilPanelRepository | None = None,
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
    app = create_service_app(
        config,
        title="Media Center Web Cabinet",
        audit_sink=resolved_tenant_audit_sink,
    )
    app.state.web_cabinet_api = WebCabinetAPIState(
        repository=repository or InMemoryWebCabinetRepository(),
        wallet_repository=wallet_repository or InMemoryWalletRepository(),
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
    app.include_router(router)
    return app


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


def _render_cabinet_html(overview: WebCabinetOverviewResponse) -> str:
    operations = _render_operations(overview.operations)
    content = _render_content(overview.content)
    links = _render_links(overview.referral_links)
    identity = f"{_escape(overview.member_id)} · {_escape(overview.tenant_id)}"
    balance_mcv = _format_mcv(overview.balance.balance_mcv)
    credited_mcv = _format_mcv(overview.balance.credited_mcv)
    debited_mcv = _format_mcv(overview.balance.debited_mcv)
    avg_points = _format_float(overview.contribution.avg_points_council)
    return f"""<!doctype html>
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
</html>"""


def _render_council_panel_html(overview: CouncilPanelOverviewResponse) -> str:
    identity = f"{_escape(overview.tenant_id)} · роль {_escape(overview.role)}"
    two_factor_label = "2FA active" if overview.two_factor.active else "2FA required"
    status_line = f"{two_factor_label} · {_format_datetime(overview.generated_at)}"
    queue = _render_council_queue(overview.payouts)
    details = _render_council_details(overview.payouts[0] if overview.payouts else None)
    policies = _render_council_policies(overview.policies)
    return f"""<!doctype html>
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
</html>"""


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


def _operation_type_label(operation_type: WalletOperationType) -> str:
    labels = {
        WalletOperationType.DISTRIBUTION_CREDIT: "Начисление",
        WalletOperationType.PAYOUT_DEBIT: "Списание",
        WalletOperationType.MANUAL_ADJUSTMENT: "Корректировка",
    }
    return labels[operation_type]


def _format_mcv(value: Decimal) -> str:
    return f"{value:.2f}"


def _format_signed_mcv(value: Decimal) -> str:
    sign = "+" if value >= Decimal("0") else ""
    return f"{sign}{_format_mcv(value)}"


def _format_float(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _format_share(value: float) -> str:
    return f"{value * 100:.1f}%"


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
