from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Path, Query, Request
from fastapi import status as http_status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field, model_validator

from libs.shared import (
    BOARD_ROLE,
    COUNCIL_ROLE,
    MEMBER_ASSOC_ROLE,
    MEMBER_FULL_ROLE,
    PRESIDIUM_ROLE,
    VALIDATION_ERROR_CODE,
    AccessPolicy,
    AuditLogger,
    BaseAppConfig,
    InMemoryAuditLogSink,
    InMemoryAuditSink,
    InMemoryEventBus,
    InMemoryTenantVectorStore,
    JSONValue,
    ServiceTemplateConfig,
    SharedBaseModel,
    SharedError,
    SubjectId,
    TenantContext,
    TenantCoreError,
    TenantVectorStore,
    create_service_runtime_app,
    error_response_body,
    require_access,
    require_tenant_context,
)

from .orchestrator import (
    AgenticRagQueryRequest,
    AgentRun,
    AgentRunAlreadyExistsError,
    AgentRunInput,
    AgentStatusResponse,
    AgentTaskType,
    AudienceSource,
    AutoReplyRequest,
    ContentAgentActionRequest,
    ContentHygieneRequest,
    CouncilThresholds,
    DecisionExplanationListResponse,
    DeepResearchRequest,
    InMemoryNeuroAgentRepository,
    NeuroAgentOrchestrator,
    NeuroAgentOrchestratorError,
    PdnScopeViolationError,
    PublicationOptimizationRequest,
    RagDocumentInput,
    RagDocumentsUpsertResult,
    ThresholdUpdateInput,
)
from .proxy_rotation import (
    InMemoryProxyPoolRepository,
    ProxyEndpointConfig,
    ProxyHealthCheckResult,
    ProxyHealthSignal,
    ProxyLease,
    ProxyPoolNotFoundError,
    ProxyPoolState,
    ProxyRotationError,
    ProxyRotationManager,
    ProxyRotationStrategy,
    ProxyUnavailableError,
)

NEURO_AGENT_ORCHESTRATOR_SERVICE_NAME = "neuro-agent-orchestrator"

TaskTypeQuery = Annotated[AgentTaskType | None, Query(alias="task_type")]
ProxyPoolPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
    ),
]
_PLATFORM_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"

AGENT_RUN_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    BOARD_ROLE,
    MEMBER_FULL_ROLE,
    MEMBER_ASSOC_ROLE,
    action="neuro_agent.run",
    resource_type="neuro_agent_orchestrator",
)
AGENT_STATUS_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    action="neuro_agent.status.read",
    resource_type="neuro_agent_orchestrator",
)
DECISION_EXPLANATION_READ_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    action="neuro_agent.decision_explanations.read",
    resource_type="neuro_agent_decision_explanation",
)
THRESHOLD_READ_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    MEMBER_FULL_ROLE,
    MEMBER_ASSOC_ROLE,
    action="neuro_agent.thresholds.read",
    resource_type="neuro_agent_orchestrator",
)
THRESHOLD_UPDATE_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    action="neuro_agent.thresholds.update",
    resource_type="neuro_agent_orchestrator",
)
PROXY_POOL_READ_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    MEMBER_FULL_ROLE,
    MEMBER_ASSOC_ROLE,
    action="neuro_agent.proxy_pool.read",
    resource_type="neuro_agent_proxy_pool",
)
PROXY_POOL_MANAGE_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    BOARD_ROLE,
    action="neuro_agent.proxy_pool.manage",
    resource_type="neuro_agent_proxy_pool",
)
PROXY_HEALTH_CHECK_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    BOARD_ROLE,
    action="neuro_agent.proxy_pool.health_check",
    resource_type="neuro_agent_proxy_pool",
)
PROXY_LEASE_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    BOARD_ROLE,
    MEMBER_FULL_ROLE,
    MEMBER_ASSOC_ROLE,
    action="neuro_agent.proxy_pool.lease",
    resource_type="neuro_agent_proxy_pool",
)
RAG_DOCUMENT_UPSERT_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    BOARD_ROLE,
    MEMBER_FULL_ROLE,
    MEMBER_ASSOC_ROLE,
    action="neuro_agent.rag.documents.upsert",
    resource_type="neuro_agent_rag",
)


class UpdateThresholdsRequest(SharedBaseModel):
    max_autonomous_risk_score: float | None = Field(
        default=None,
        ge=0,
        le=1,
        allow_inf_nan=False,
    )
    min_agent_confidence: float | None = Field(
        default=None,
        ge=0,
        le=1,
        allow_inf_nan=False,
    )
    max_autonomous_recipients: int | None = Field(default=None, ge=1, le=10_000)
    min_content_quality_score: float | None = Field(
        default=None,
        ge=0,
        le=1,
        allow_inf_nan=False,
    )
    allowed_template_keys: tuple[str, ...] | None = None
    updated_at: datetime | None = None
    event_id: str | None = Field(default=None, min_length=1, max_length=128)
    metadata: dict[str, JSONValue] = Field(default_factory=dict)


class UpsertRagDocumentsRequest(SharedBaseModel):
    documents: tuple[RagDocumentInput, ...] = Field(min_length=1)
    event_id: str | None = Field(default=None, min_length=1, max_length=128)
    updated_at: datetime | None = None


class RunAgentRequest(SharedBaseModel):
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    event_id: str | None = Field(default=None, min_length=1, max_length=128)
    task_type: AgentTaskType
    audience_sources: tuple[AudienceSource, ...] = Field(default_factory=tuple)
    auto_reply: AutoReplyRequest | None = None
    content_hygiene: ContentHygieneRequest | None = None
    publication_optimization: PublicationOptimizationRequest | None = None
    rag_query: AgenticRagQueryRequest | None = None
    deep_research: DeepResearchRequest | None = None
    content_agent_action: ContentAgentActionRequest | None = None
    created_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_task_payload(self) -> RunAgentRequest:
        if (
            self.task_type is AgentTaskType.AUDIENCE_ANALYSIS
            and len(self.audience_sources) == 0
        ):
            raise ValueError("audience_sources обязателен для audience_analysis")
        if (
            self.task_type is AgentTaskType.ENGAGEMENT_AUTO_REPLY
            and self.auto_reply is None
        ):
            raise ValueError("auto_reply обязателен для engagement_auto_reply")
        if (
            self.task_type is AgentTaskType.CONTENT_HYGIENE
            and self.content_hygiene is None
        ):
            raise ValueError("content_hygiene обязателен для content_hygiene")
        if (
            self.task_type is AgentTaskType.PUBLICATION_OPTIMIZATION
            and self.publication_optimization is None
        ):
            raise ValueError(
                "publication_optimization обязателен для publication_optimization"
            )
        if self.task_type is AgentTaskType.AGENTIC_RAG and self.rag_query is None:
            raise ValueError("rag_query обязателен для agentic_rag")
        if self.task_type is AgentTaskType.DEEP_RESEARCH and self.deep_research is None:
            raise ValueError("deep_research обязателен для deep_research")
        if (
            self.task_type is AgentTaskType.CONTENT_AGENT_ACTION
            and self.content_agent_action is None
        ):
            raise ValueError("content_agent_action обязателен для content_agent_action")

        return self


class UpsertProxyPoolRequest(SharedBaseModel):
    platform: str = Field(pattern=_PLATFORM_PATTERN)
    proxies: tuple[ProxyEndpointConfig, ...] = Field(min_length=1)
    rotation_strategy: ProxyRotationStrategy = ProxyRotationStrategy.ROUND_ROBIN
    updated_at: datetime | None = None
    event_id: str | None = Field(default=None, min_length=1, max_length=128)
    metadata: dict[str, JSONValue] = Field(default_factory=dict)


class LeaseProxyRequest(SharedBaseModel):
    selected_at: datetime | None = None
    event_id: str | None = Field(default=None, min_length=1, max_length=128)


class CheckProxyHealthRequest(SharedBaseModel):
    checks: tuple[ProxyHealthSignal, ...] = Field(min_length=1)
    event_id: str | None = Field(default=None, min_length=1, max_length=128)


@dataclass(slots=True)
class NeuroAgentOrchestratorAPIState:
    orchestrator: NeuroAgentOrchestrator
    proxy_rotation: ProxyRotationManager
    publisher: InMemoryEventBus
    repository: InMemoryNeuroAgentRepository
    proxy_repository: InMemoryProxyPoolRepository
    vector_store: TenantVectorStore
    audit_log_sink: InMemoryAuditLogSink
    tenant_audit_sink: InMemoryAuditSink


router = APIRouter(tags=["Neuro-Agent Orchestrator"])


def create_neuro_agent_orchestrator_app(
    config: BaseAppConfig | ServiceTemplateConfig,
    *,
    repository: InMemoryNeuroAgentRepository | None = None,
    proxy_repository: InMemoryProxyPoolRepository | None = None,
    vector_store: TenantVectorStore | None = None,
    publisher: InMemoryEventBus | None = None,
    audit_log_sink: InMemoryAuditLogSink | None = None,
    tenant_audit_sink: InMemoryAuditSink | None = None,
) -> FastAPI:
    resolved_repository = repository or InMemoryNeuroAgentRepository()
    resolved_proxy_repository = proxy_repository or InMemoryProxyPoolRepository()
    resolved_vector_store = vector_store or InMemoryTenantVectorStore()
    resolved_publisher = publisher or InMemoryEventBus()
    resolved_audit_log_sink = audit_log_sink or InMemoryAuditLogSink()
    resolved_tenant_audit_sink = tenant_audit_sink or InMemoryAuditSink()
    orchestrator = NeuroAgentOrchestrator(
        repository=resolved_repository,
        publisher=resolved_publisher,
        audit_logger=AuditLogger(sink=resolved_audit_log_sink),
        vector_store=resolved_vector_store,
    )
    proxy_rotation = ProxyRotationManager(
        repository=resolved_proxy_repository,
        publisher=resolved_publisher,
        audit_logger=AuditLogger(sink=resolved_audit_log_sink),
    )
    app = create_service_runtime_app(
        config,
        title="Media Center Neuro-Agent Orchestrator",
        audit_sink=resolved_tenant_audit_sink,
    )
    app.state.neuro_agent_api = NeuroAgentOrchestratorAPIState(
        orchestrator=orchestrator,
        proxy_rotation=proxy_rotation,
        publisher=resolved_publisher,
        repository=resolved_repository,
        proxy_repository=resolved_proxy_repository,
        vector_store=resolved_vector_store,
        audit_log_sink=resolved_audit_log_sink,
        tenant_audit_sink=resolved_tenant_audit_sink,
    )
    app.add_exception_handler(TenantCoreError, _tenant_core_error_handler)
    app.add_exception_handler(SharedError, _shared_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(PdnScopeViolationError, _pdn_scope_violation_handler)
    app.add_exception_handler(
        AgentRunAlreadyExistsError,
        _agent_run_already_exists_handler,
    )
    app.add_exception_handler(
        NeuroAgentOrchestratorError,
        _neuro_agent_error_handler,
    )
    app.add_exception_handler(ProxyPoolNotFoundError, _proxy_pool_not_found_handler)
    app.add_exception_handler(ProxyUnavailableError, _proxy_unavailable_handler)
    app.add_exception_handler(ProxyRotationError, _proxy_rotation_error_handler)
    app.add_exception_handler(ValueError, _value_error_handler)
    app.include_router(router)
    return app


@router.post(
    "/rag/documents",
    response_model=RagDocumentsUpsertResult,
    status_code=http_status.HTTP_201_CREATED,
    summary="Добавить tenant-scoped документы в Agentic RAG",
)
async def upsert_rag_documents(
    payload: UpsertRagDocumentsRequest,
    state: Annotated[NeuroAgentOrchestratorAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> RagDocumentsUpsertResult:
    actor_context = require_access(RAG_DOCUMENT_UPSERT_POLICY, context=context)
    return await state.orchestrator.upsert_rag_documents(
        tenant_id=context.tenant_id,
        updated_by=_subject(actor_context),
        correlation_id=_correlation_id(context),
        documents=payload.documents,
        updated_at=payload.updated_at,
        event_id=payload.event_id,
    )


@router.post(
    "/agents/run",
    response_model=AgentRun,
    status_code=http_status.HTTP_201_CREATED,
    summary="Запустить задачу Neuro-Agent Orchestrator",
)
async def run_agent(
    payload: RunAgentRequest,
    state: Annotated[NeuroAgentOrchestratorAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> AgentRun:
    actor_context = require_access(AGENT_RUN_POLICY, context=context)
    return await state.orchestrator.run_agent(
        tenant_id=context.tenant_id,
        created_by=_subject(actor_context),
        correlation_id=_correlation_id(context),
        run=AgentRunInput(
            run_id=payload.run_id,
            event_id=payload.event_id,
            task_type=payload.task_type,
            audience_sources=payload.audience_sources,
            auto_reply=payload.auto_reply,
            content_hygiene=payload.content_hygiene,
            publication_optimization=payload.publication_optimization,
            rag_query=payload.rag_query,
            deep_research=payload.deep_research,
            content_agent_action=payload.content_agent_action,
            created_at=payload.created_at,
        ),
    )


@router.get(
    "/agents/explanations",
    response_model=DecisionExplanationListResponse,
    summary="Получить XAI-журнал объяснений решений AI",
)
def agent_decision_explanations(
    state: Annotated[NeuroAgentOrchestratorAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
    task_type: TaskTypeQuery = None,
) -> DecisionExplanationListResponse:
    require_access(DECISION_EXPLANATION_READ_POLICY, context=context)
    return DecisionExplanationListResponse(
        items=state.orchestrator.list_decision_explanations(
            tenant_id=context.tenant_id,
            task_type=task_type,
        )
    )


@router.get(
    "/agents/status",
    response_model=AgentStatusResponse,
    summary="Получить историю запусков Neuro-Agent Orchestrator",
)
def agents_status(
    state: Annotated[NeuroAgentOrchestratorAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
    task_type: TaskTypeQuery = None,
) -> AgentStatusResponse:
    require_access(AGENT_STATUS_POLICY, context=context)
    return AgentStatusResponse(
        items=state.orchestrator.list_runs(
            tenant_id=context.tenant_id,
            task_type=task_type,
        )
    )


@router.get(
    "/thresholds",
    response_model=CouncilThresholds,
    summary="Получить пороги Совета для автономных AI-действий",
)
def get_thresholds(
    state: Annotated[NeuroAgentOrchestratorAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> CouncilThresholds:
    require_access(THRESHOLD_READ_POLICY, context=context)
    return state.orchestrator.get_thresholds(tenant_id=context.tenant_id)


@router.put(
    "/thresholds",
    response_model=CouncilThresholds,
    summary="Обновить пороги Совета для Neuro-Agent Orchestrator",
)
async def update_thresholds(
    payload: UpdateThresholdsRequest,
    state: Annotated[NeuroAgentOrchestratorAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> CouncilThresholds:
    actor_context = require_access(THRESHOLD_UPDATE_POLICY, context=context)
    return await state.orchestrator.update_thresholds(
        tenant_id=context.tenant_id,
        updated_by=_subject(actor_context),
        correlation_id=_correlation_id(context),
        update=ThresholdUpdateInput(
            max_autonomous_risk_score=payload.max_autonomous_risk_score,
            min_agent_confidence=payload.min_agent_confidence,
            max_autonomous_recipients=payload.max_autonomous_recipients,
            min_content_quality_score=payload.min_content_quality_score,
            allowed_template_keys=payload.allowed_template_keys,
            metadata=payload.metadata,
        ),
        updated_at=payload.updated_at,
        event_id=payload.event_id,
    )


@router.put(
    "/proxy-pools/{pool_id}",
    response_model=ProxyPoolState,
    summary="Создать или заменить tenant-scoped пул прокси",
)
async def upsert_proxy_pool(
    pool_id: ProxyPoolPath,
    payload: UpsertProxyPoolRequest,
    state: Annotated[NeuroAgentOrchestratorAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> ProxyPoolState:
    actor_context = require_access(PROXY_POOL_MANAGE_POLICY, context=context)
    return await state.proxy_rotation.upsert_pool(
        tenant_id=context.tenant_id,
        pool_id=pool_id,
        platform=payload.platform,
        proxies=payload.proxies,
        rotation_strategy=payload.rotation_strategy,
        updated_by=_subject(actor_context),
        correlation_id=_correlation_id(context),
        metadata=payload.metadata,
        updated_at=payload.updated_at,
        event_id=payload.event_id,
    )


@router.get(
    "/proxy-pools/{pool_id}",
    response_model=ProxyPoolState,
    summary="Получить состояние tenant-scoped пула прокси",
)
def get_proxy_pool(
    pool_id: ProxyPoolPath,
    state: Annotated[NeuroAgentOrchestratorAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> ProxyPoolState:
    require_access(PROXY_POOL_READ_POLICY, context=context)
    return state.proxy_rotation.get_pool(
        tenant_id=context.tenant_id,
        pool_id=pool_id,
    )


@router.post(
    "/proxy-pools/{pool_id}/lease",
    response_model=ProxyLease,
    status_code=http_status.HTTP_201_CREATED,
    summary="Выдать следующий живой прокси из tenant-scoped пула",
)
async def lease_proxy(
    pool_id: ProxyPoolPath,
    payload: LeaseProxyRequest,
    state: Annotated[NeuroAgentOrchestratorAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> ProxyLease:
    actor_context = require_access(PROXY_LEASE_POLICY, context=context)
    return await state.proxy_rotation.lease_proxy(
        tenant_id=context.tenant_id,
        pool_id=pool_id,
        leased_by=_subject(actor_context),
        correlation_id=_correlation_id(context),
        selected_at=payload.selected_at,
        event_id=payload.event_id,
    )


@router.post(
    "/proxy-pools/{pool_id}/health-checks",
    response_model=ProxyHealthCheckResult,
    summary="Зафиксировать результаты проверки живости прокси",
)
async def check_proxy_health(
    pool_id: ProxyPoolPath,
    payload: CheckProxyHealthRequest,
    state: Annotated[NeuroAgentOrchestratorAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> ProxyHealthCheckResult:
    actor_context = require_access(PROXY_HEALTH_CHECK_POLICY, context=context)
    return await state.proxy_rotation.check_pool_health(
        tenant_id=context.tenant_id,
        pool_id=pool_id,
        checks=payload.checks,
        checked_by=_subject(actor_context),
        correlation_id=_correlation_id(context),
        event_id=payload.event_id,
    )


def _api_state(request: Request) -> NeuroAgentOrchestratorAPIState:
    return cast(NeuroAgentOrchestratorAPIState, request.app.state.neuro_agent_api)


def _tenant_context() -> TenantContext:
    return require_tenant_context()


def _subject(context: TenantContext) -> SubjectId:
    if context.subject is None or context.subject.strip() == "":
        raise SharedError(
            status_code=400,
            error_code="actor_required",
            message=(
                "Операция Neuro-Agent Orchestrator требует subject в tenant context"
            ),
            correlation_id=context.correlation_id,
        )

    return context.subject


def _correlation_id(context: TenantContext) -> str:
    return context.correlation_id or f"corr-{uuid4()}"


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


async def _pdn_scope_violation_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    error = cast(PdnScopeViolationError, exc)
    return JSONResponse(
        status_code=422,
        content=error_response_body(
            code="pdn_scope_violation",
            message=str(error),
            details={"reasons": list(error.reasons)},
        ),
    )


async def _agent_run_already_exists_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content=error_response_body(
            code="agent_run_already_exists",
            message=str(exc),
        ),
    )


async def _neuro_agent_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=error_response_body(
            code="neuro_agent_orchestrator_error",
            message=str(exc),
        ),
    )


async def _proxy_pool_not_found_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content=error_response_body(
            code="proxy_pool_not_found",
            message=str(exc),
        ),
    )


async def _proxy_unavailable_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
        content=error_response_body(
            code="proxy_unavailable",
            message=str(exc),
        ),
    )


async def _proxy_rotation_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=error_response_body(
            code="proxy_rotation_error",
            message=str(exc),
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
