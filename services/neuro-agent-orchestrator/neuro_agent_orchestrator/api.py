from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Query, Request
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
    create_service_app,
    error_response_body,
    require_access,
    require_tenant_context,
)

from .orchestrator import (
    AgentRun,
    AgentRunAlreadyExistsError,
    AgentRunInput,
    AgentStatusResponse,
    AgentTaskType,
    AudienceSource,
    AutoReplyRequest,
    ContentHygieneRequest,
    CouncilThresholds,
    InMemoryNeuroAgentRepository,
    NeuroAgentOrchestrator,
    NeuroAgentOrchestratorError,
    PdnScopeViolationError,
    PublicationOptimizationRequest,
    ThresholdUpdateInput,
)

NEURO_AGENT_ORCHESTRATOR_SERVICE_NAME = "neuro-agent-orchestrator"

TaskTypeQuery = Annotated[AgentTaskType | None, Query(alias="task_type")]

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


class RunAgentRequest(SharedBaseModel):
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    event_id: str | None = Field(default=None, min_length=1, max_length=128)
    task_type: AgentTaskType
    audience_sources: tuple[AudienceSource, ...] = Field(default_factory=tuple)
    auto_reply: AutoReplyRequest | None = None
    content_hygiene: ContentHygieneRequest | None = None
    publication_optimization: PublicationOptimizationRequest | None = None
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

        return self


@dataclass(slots=True)
class NeuroAgentOrchestratorAPIState:
    orchestrator: NeuroAgentOrchestrator
    publisher: InMemoryEventBus
    repository: InMemoryNeuroAgentRepository
    audit_log_sink: InMemoryAuditLogSink
    tenant_audit_sink: InMemoryAuditSink


router = APIRouter(tags=["Neuro-Agent Orchestrator"])


def create_neuro_agent_orchestrator_app(
    config: ServiceTemplateConfig,
    *,
    repository: InMemoryNeuroAgentRepository | None = None,
    publisher: InMemoryEventBus | None = None,
    audit_log_sink: InMemoryAuditLogSink | None = None,
    tenant_audit_sink: InMemoryAuditSink | None = None,
) -> FastAPI:
    resolved_repository = repository or InMemoryNeuroAgentRepository()
    resolved_publisher = publisher or InMemoryEventBus()
    resolved_audit_log_sink = audit_log_sink or InMemoryAuditLogSink()
    resolved_tenant_audit_sink = tenant_audit_sink or InMemoryAuditSink()
    orchestrator = NeuroAgentOrchestrator(
        repository=resolved_repository,
        publisher=resolved_publisher,
        audit_logger=AuditLogger(sink=resolved_audit_log_sink),
    )
    app = create_service_app(
        config,
        title="Media Center Neuro-Agent Orchestrator",
        audit_sink=resolved_tenant_audit_sink,
    )
    app.state.neuro_agent_api = NeuroAgentOrchestratorAPIState(
        orchestrator=orchestrator,
        publisher=resolved_publisher,
        repository=resolved_repository,
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
    app.add_exception_handler(ValueError, _value_error_handler)
    app.include_router(router)
    return app


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
            created_at=payload.created_at,
        ),
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


async def _value_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=error_response_body(
            code=VALIDATION_ERROR_CODE,
            message=str(exc),
        ),
    )
