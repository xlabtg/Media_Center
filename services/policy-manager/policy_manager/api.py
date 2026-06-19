from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Request
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

from .manager import (
    InMemoryPolicyRepository,
    PolicyApplicationResult,
    PolicyApplyInput,
    PolicyHistoryResponse,
    PolicyListResponse,
    PolicyManager,
    PolicyManagerError,
    PolicyNotFoundError,
    PolicyRecord,
    PolicyUpdateInput,
)

POLICY_MANAGER_SERVICE_NAME = "policy-manager"

POLICY_READ_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    MEMBER_FULL_ROLE,
    MEMBER_ASSOC_ROLE,
    action="policy.read",
    resource_type="policy",
)
POLICY_APPLY_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    MEMBER_FULL_ROLE,
    MEMBER_ASSOC_ROLE,
    action="policy.apply",
    resource_type="policy",
)
POLICY_UPDATE_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    action="policy.update",
    resource_type="policy",
)


class UpdatePolicyRequest(SharedBaseModel):
    value: dict[str, JSONValue]
    updated_at: datetime | None = None
    event_id: str | None = Field(default=None, min_length=1, max_length=128)
    metadata: dict[str, JSONValue] = Field(default_factory=dict)


class ApplyPoliciesRequest(SharedBaseModel):
    policy_keys: tuple[str, ...] = Field(min_length=1, max_length=32)
    facts: dict[str, JSONValue] = Field(default_factory=dict)
    applied_at: datetime | None = None


@dataclass(slots=True)
class PolicyManagerAPIState:
    policy_manager: PolicyManager
    publisher: InMemoryEventBus
    repository: InMemoryPolicyRepository
    audit_log_sink: InMemoryAuditLogSink
    tenant_audit_sink: InMemoryAuditSink


router = APIRouter(tags=["Policy Manager"])


def create_policy_manager_app(
    config: ServiceTemplateConfig,
    *,
    repository: InMemoryPolicyRepository | None = None,
    publisher: InMemoryEventBus | None = None,
    audit_log_sink: InMemoryAuditLogSink | None = None,
    tenant_audit_sink: InMemoryAuditSink | None = None,
) -> FastAPI:
    resolved_repository = repository or InMemoryPolicyRepository()
    resolved_publisher = publisher or InMemoryEventBus()
    resolved_audit_log_sink = audit_log_sink or InMemoryAuditLogSink()
    resolved_tenant_audit_sink = tenant_audit_sink or InMemoryAuditSink()
    policy_manager = PolicyManager(
        repository=resolved_repository,
        publisher=resolved_publisher,
        audit_logger=AuditLogger(sink=resolved_audit_log_sink),
    )
    app = create_service_app(
        config,
        title="Media Center Policy Manager",
        audit_sink=resolved_tenant_audit_sink,
    )
    app.state.policy_manager_api = PolicyManagerAPIState(
        policy_manager=policy_manager,
        publisher=resolved_publisher,
        repository=resolved_repository,
        audit_log_sink=resolved_audit_log_sink,
        tenant_audit_sink=resolved_tenant_audit_sink,
    )
    app.add_exception_handler(TenantCoreError, _tenant_core_error_handler)
    app.add_exception_handler(SharedError, _shared_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(PolicyNotFoundError, _policy_not_found_error_handler)
    app.add_exception_handler(PolicyManagerError, _policy_manager_error_handler)
    app.add_exception_handler(ValueError, _value_error_handler)
    app.include_router(router)
    return app


@router.get(
    "/policies",
    response_model=PolicyListResponse,
    summary="Получить актуальные политики tenant",
)
def list_policies(
    state: Annotated[PolicyManagerAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> PolicyListResponse:
    require_access(POLICY_READ_POLICY, context=context)
    return PolicyListResponse(
        items=state.policy_manager.list_policies(tenant_id=context.tenant_id)
    )


@router.post(
    "/policies/apply",
    response_model=PolicyApplicationResult,
    summary="Применить актуальные политики к фактам сервиса или агента",
)
def apply_policies(
    payload: ApplyPoliciesRequest,
    state: Annotated[PolicyManagerAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> PolicyApplicationResult:
    require_access(POLICY_APPLY_POLICY, context=context)
    return state.policy_manager.apply_policies(
        tenant_id=context.tenant_id,
        application=PolicyApplyInput(
            policy_keys=payload.policy_keys,
            facts=payload.facts,
        ),
        applied_at=payload.applied_at,
    )


@router.get(
    "/policies/{key}/history",
    response_model=PolicyHistoryResponse,
    summary="Получить историю версий политики tenant",
)
def policy_history(
    key: str,
    state: Annotated[PolicyManagerAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> PolicyHistoryResponse:
    require_access(POLICY_READ_POLICY, context=context)
    return PolicyHistoryResponse(
        items=state.policy_manager.get_history(
            tenant_id=context.tenant_id,
            key=key,
        )
    )


@router.put(
    "/policies/{key}",
    response_model=PolicyRecord,
    summary="Обновить политику tenant решением Совета",
)
async def update_policy(
    key: str,
    payload: UpdatePolicyRequest,
    state: Annotated[PolicyManagerAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> PolicyRecord:
    actor_context = require_access(POLICY_UPDATE_POLICY, context=context)
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


def _api_state(request: Request) -> PolicyManagerAPIState:
    return cast(PolicyManagerAPIState, request.app.state.policy_manager_api)


def _tenant_context() -> TenantContext:
    return require_tenant_context()


def _subject(context: TenantContext) -> SubjectId:
    if context.subject is None or context.subject.strip() == "":
        raise SharedError(
            status_code=400,
            error_code="actor_required",
            message="Операция Policy Manager требует subject в tenant context",
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


async def _policy_not_found_error_handler(
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


async def _value_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=error_response_body(
            code=VALIDATION_ERROR_CODE,
            message=str(exc),
        ),
    )
