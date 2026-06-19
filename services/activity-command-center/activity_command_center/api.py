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
from pydantic import Field

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

from .command_center import (
    ActivityCommandCenter,
    ActivityCommandCenterError,
    ActivityOverview,
    ActivityTask,
    FeedbackLoop,
    InMemoryActivityRepository,
    TaskCreateInput,
    TaskListResponse,
    TaskStatus,
    ThresholdSettings,
    ThresholdUpdateInput,
)

ACTIVITY_COMMAND_CENTER_SERVICE_NAME = "activity-command-center"

_TASK_TYPE_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"

TaskStatusQuery = Annotated[TaskStatus | None, Query(alias="status")]
FeedbackLoopQuery = Annotated[FeedbackLoop | None, Query(alias="feedback_loop")]

ACTIVITY_READ_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    action="activity.read",
    resource_type="activity_command_center",
)
TASK_CREATE_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    BOARD_ROLE,
    MEMBER_FULL_ROLE,
    MEMBER_ASSOC_ROLE,
    action="activity.task.create",
    resource_type="activity_command_center",
)
TASK_READ_POLICY = ACTIVITY_READ_POLICY
THRESHOLD_READ_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    MEMBER_FULL_ROLE,
    MEMBER_ASSOC_ROLE,
    action="activity.thresholds.read",
    resource_type="activity_command_center",
)
THRESHOLD_UPDATE_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    action="activity.thresholds.update",
    resource_type="activity_command_center",
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
    operational_queue_limit: int | None = Field(default=None, ge=1, le=10_000)
    strategic_queue_limit: int | None = Field(default=None, ge=1, le=10_000)
    adaptive_queue_limit: int | None = Field(default=None, ge=1, le=10_000)
    updated_at: datetime | None = None
    event_id: str | None = Field(default=None, min_length=1, max_length=128)
    metadata: dict[str, JSONValue] = Field(default_factory=dict)


class CreateTaskRequest(SharedBaseModel):
    task_id: str | None = Field(default=None, min_length=1, max_length=128)
    event_id: str | None = Field(default=None, min_length=1, max_length=128)
    task_type: str = Field(pattern=_TASK_TYPE_PATTERN)
    title: str = Field(min_length=1, max_length=256)
    payload: dict[str, JSONValue] = Field(default_factory=dict)
    assignee: SubjectId
    agent_id: SubjectId | None = None
    risk_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    agent_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    feedback_loop: FeedbackLoop
    created_at: datetime | None = None


@dataclass(slots=True)
class ActivityCommandCenterAPIState:
    command_center: ActivityCommandCenter
    publisher: InMemoryEventBus
    repository: InMemoryActivityRepository
    audit_log_sink: InMemoryAuditLogSink
    tenant_audit_sink: InMemoryAuditSink


router = APIRouter(tags=["Activity Command Center"])


def create_activity_command_center_app(
    config: ServiceTemplateConfig,
    *,
    repository: InMemoryActivityRepository | None = None,
    publisher: InMemoryEventBus | None = None,
    audit_log_sink: InMemoryAuditLogSink | None = None,
    tenant_audit_sink: InMemoryAuditSink | None = None,
) -> FastAPI:
    resolved_repository = repository or InMemoryActivityRepository()
    resolved_publisher = publisher or InMemoryEventBus()
    resolved_audit_log_sink = audit_log_sink or InMemoryAuditLogSink()
    resolved_tenant_audit_sink = tenant_audit_sink or InMemoryAuditSink()
    command_center = ActivityCommandCenter(
        repository=resolved_repository,
        publisher=resolved_publisher,
        audit_logger=AuditLogger(sink=resolved_audit_log_sink),
    )
    app = create_service_app(
        config,
        title="Media Center Activity Command Center",
        audit_sink=resolved_tenant_audit_sink,
    )
    app.state.activity_command_center_api = ActivityCommandCenterAPIState(
        command_center=command_center,
        publisher=resolved_publisher,
        repository=resolved_repository,
        audit_log_sink=resolved_audit_log_sink,
        tenant_audit_sink=resolved_tenant_audit_sink,
    )
    app.add_exception_handler(TenantCoreError, _tenant_core_error_handler)
    app.add_exception_handler(SharedError, _shared_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(
        ActivityCommandCenterError,
        _activity_command_center_error_handler,
    )
    app.add_exception_handler(ValueError, _value_error_handler)
    app.include_router(router)
    return app


@router.get(
    "/activity/overview",
    response_model=ActivityOverview,
    summary="Получить сводку активности tenant",
)
def activity_overview(
    state: Annotated[ActivityCommandCenterAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> ActivityOverview:
    require_access(ACTIVITY_READ_POLICY, context=context)
    return state.command_center.overview(tenant_id=context.tenant_id)


@router.post(
    "/tasks",
    response_model=ActivityTask,
    status_code=http_status.HTTP_201_CREATED,
    summary="Создать задачу в очереди Activity Command Center",
)
async def create_task(
    payload: CreateTaskRequest,
    state: Annotated[ActivityCommandCenterAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> ActivityTask:
    actor_context = require_access(TASK_CREATE_POLICY, context=context)
    return await state.command_center.create_task(
        tenant_id=context.tenant_id,
        created_by=_subject(actor_context),
        correlation_id=_correlation_id(context),
        task=TaskCreateInput(
            task_id=payload.task_id,
            event_id=payload.event_id,
            task_type=payload.task_type,
            title=payload.title,
            payload=payload.payload,
            assignee=payload.assignee,
            agent_id=payload.agent_id,
            risk_score=payload.risk_score,
            agent_confidence=payload.agent_confidence,
            feedback_loop=payload.feedback_loop,
        ),
        created_at=payload.created_at,
    )


@router.get(
    "/tasks",
    response_model=TaskListResponse,
    summary="Получить задачи tenant с фильтрами очереди",
)
def list_tasks(
    state: Annotated[ActivityCommandCenterAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
    status_filter: TaskStatusQuery = None,
    feedback_loop: FeedbackLoopQuery = None,
) -> TaskListResponse:
    require_access(TASK_READ_POLICY, context=context)
    return TaskListResponse(
        items=state.command_center.list_tasks(
            tenant_id=context.tenant_id,
            status=status_filter,
            feedback_loop=feedback_loop,
        )
    )


@router.get(
    "/thresholds",
    response_model=ThresholdSettings,
    summary="Получить пороги Совета tenant",
)
def get_thresholds(
    state: Annotated[ActivityCommandCenterAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> ThresholdSettings:
    require_access(THRESHOLD_READ_POLICY, context=context)
    return state.command_center.get_thresholds(tenant_id=context.tenant_id)


@router.put(
    "/thresholds",
    response_model=ThresholdSettings,
    summary="Обновить пороги Совета tenant",
)
async def update_thresholds(
    payload: UpdateThresholdsRequest,
    state: Annotated[ActivityCommandCenterAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> ThresholdSettings:
    actor_context = require_access(THRESHOLD_UPDATE_POLICY, context=context)
    return await state.command_center.update_thresholds(
        tenant_id=context.tenant_id,
        updated_by=_subject(actor_context),
        correlation_id=_correlation_id(context),
        update=ThresholdUpdateInput(
            max_autonomous_risk_score=payload.max_autonomous_risk_score,
            min_agent_confidence=payload.min_agent_confidence,
            operational_queue_limit=payload.operational_queue_limit,
            strategic_queue_limit=payload.strategic_queue_limit,
            adaptive_queue_limit=payload.adaptive_queue_limit,
            metadata=payload.metadata,
        ),
        updated_at=payload.updated_at,
        event_id=payload.event_id,
    )


def _api_state(request: Request) -> ActivityCommandCenterAPIState:
    return cast(
        ActivityCommandCenterAPIState,
        request.app.state.activity_command_center_api,
    )


def _tenant_context() -> TenantContext:
    return require_tenant_context()


def _subject(context: TenantContext) -> SubjectId:
    if context.subject is None or context.subject.strip() == "":
        raise SharedError(
            status_code=400,
            error_code="actor_required",
            message="Операция Activity Command Center требует subject в tenant context",
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


async def _activity_command_center_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=error_response_body(
            code="activity_command_center_error",
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
