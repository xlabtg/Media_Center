from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Path, Query, Request
from fastapi import status as http_status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field

from libs.shared import (
    COUNCIL_ROLE,
    VALIDATION_ERROR_CODE,
    AccessPolicy,
    AuditHash,
    AuditLogger,
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
    TOTPService,
    create_service_app,
    error_response_body,
    require_access,
    require_tenant_context,
)

from .confirmation_manager import (
    PAYOUT_CONFIRM_OPERATION,
    PAYOUT_CONFIRM_POLICY,
    PayoutConfirmation,
    PayoutConfirmationManager,
)
from .execution_manager import (
    PaymentConnector,
    PayoutConnectorError,
    PayoutExecutionManager,
    PayoutExecutionReceipt,
    PayoutPaymentStatusReceipt,
)
from .queue_manager import (
    InMemoryPayoutQueueRepository,
    PayoutNotExecutableError,
    PayoutNotFoundError,
    PayoutQueueError,
    PayoutQueueItem,
    PayoutQueueManager,
    PayoutStatus,
    resolve_veto_window_hours,
)
from .veto_manager import VetoDecision, VetoManager, VetoWindowClosedError

HITL_PAYOUT_GATEWAY_SERVICE_NAME = "hitl-payout-gateway"

_PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
_REASON_CODE_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
_TOKEN_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"
_TOTP_CODE_PATTERN = r"^\d{6,8}$"

PayoutIdPath = Annotated[
    str,
    Path(min_length=1, max_length=128, pattern=_TOKEN_PATTERN),
]
PayoutStatusQuery = Annotated[PayoutStatus | None, Query(alias="status")]

PAYOUT_QUEUE_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    action="payout.queue",
    resource_type="hitl_payout",
)
PAYOUT_READ_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    action="payout.read",
    resource_type="hitl_payout",
)
PAYOUT_VETO_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    action="payout.veto",
    resource_type="hitl_payout",
)
PAYOUT_EXECUTE_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    action="payout.execute",
    resource_type="hitl_payout",
)
PAYOUT_SYNC_STATUS_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    action="payout.sync_status",
    resource_type="hitl_payout",
)


class QueuePayoutRequest(SharedBaseModel):
    payout_id: IdempotencyKey | None = None
    event_id: IdempotencyKey | None = None
    member_id: SubjectId
    period: str = Field(pattern=_PERIOD_PATTERN)
    payout_share: float = Field(ge=0, le=1, allow_inf_nan=False)
    distribution_id: IdempotencyKey
    distribution_hash: AuditHash
    created_by: SubjectId | None = None
    now: datetime | None = None
    requires_2fa: bool = True
    metadata: dict[str, JSONValue] = Field(default_factory=dict)


class VetoPayoutRequest(SharedBaseModel):
    decision_id: IdempotencyKey | None = None
    event_id: IdempotencyKey | None = None
    actor_id: SubjectId | None = None
    reason_code: str = Field(pattern=_REASON_CODE_PATTERN)
    reason: str = Field(min_length=1, max_length=512)
    now: datetime | None = None
    metadata: dict[str, JSONValue] = Field(default_factory=dict)


class ConfirmPayoutRequest(SharedBaseModel):
    confirmation_id: IdempotencyKey | None = None
    event_id: IdempotencyKey | None = None
    totp_code: str = Field(min_length=6, max_length=8, pattern=_TOTP_CODE_PATTERN)
    confirmed_at: int | None = Field(default=None, ge=0)
    metadata: dict[str, JSONValue] = Field(default_factory=dict)


class ExecutePayoutRequest(SharedBaseModel):
    execution_id: IdempotencyKey | None = None
    event_id: IdempotencyKey | None = None
    failure_event_id: IdempotencyKey | None = None
    notification_id: IdempotencyKey | None = None
    now: datetime | None = None
    metadata: dict[str, JSONValue] = Field(default_factory=dict)


class SyncPayoutStatusRequest(SharedBaseModel):
    event_id: IdempotencyKey | None = None
    failure_event_id: IdempotencyKey | None = None
    now: datetime | None = None
    metadata: dict[str, JSONValue] = Field(default_factory=dict)


class PayoutListResponse(SharedBaseModel):
    items: tuple[PayoutQueueItem, ...]


@dataclass(slots=True)
class HITLPayoutAPIState:
    queue_manager: PayoutQueueManager
    veto_manager: VetoManager
    confirmation_manager: PayoutConfirmationManager
    execution_manager: PayoutExecutionManager
    publisher: InMemoryEventBus
    audit_log_sink: InMemoryAuditLogSink
    tenant_audit_sink: InMemoryAuditSink
    totp_secrets: Mapping[tuple[str, str], str]


router = APIRouter(tags=["HITL Payout Gateway"])


def create_hitl_payout_app(
    config: ServiceTemplateConfig,
    *,
    repository: InMemoryPayoutQueueRepository | None = None,
    publisher: InMemoryEventBus | None = None,
    audit_log_sink: InMemoryAuditLogSink | None = None,
    tenant_audit_sink: InMemoryAuditSink | None = None,
    totp_secrets: Mapping[tuple[str, str], str] | None = None,
    payment_connector: PaymentConnector | None = None,
    veto_window_hours: int | None = None,
) -> FastAPI:
    resolved_repository = repository or InMemoryPayoutQueueRepository()
    resolved_publisher = publisher or InMemoryEventBus()
    resolved_audit_log_sink = audit_log_sink or InMemoryAuditLogSink()
    resolved_tenant_audit_sink = tenant_audit_sink or InMemoryAuditSink()
    audit_logger = AuditLogger(sink=resolved_audit_log_sink)
    queue_manager = PayoutQueueManager(
        publisher=resolved_publisher,
        repository=resolved_repository,
        audit_logger=audit_logger,
        veto_window_hours=(
            resolve_veto_window_hours()
            if veto_window_hours is None
            else veto_window_hours
        ),
    )
    veto_manager = VetoManager(
        repository=resolved_repository,
        publisher=resolved_publisher,
        audit_logger=audit_logger,
    )
    confirmation_manager = PayoutConfirmationManager(
        repository=resolved_repository,
        publisher=resolved_publisher,
        audit_logger=audit_logger,
    )
    execution_manager = (
        PayoutExecutionManager(
            repository=resolved_repository,
            publisher=resolved_publisher,
            audit_logger=audit_logger,
            payment_connector=payment_connector,
        )
        if payment_connector is not None
        else PayoutExecutionManager(
            repository=resolved_repository,
            publisher=resolved_publisher,
            audit_logger=audit_logger,
        )
    )

    app = create_service_app(
        config,
        title="Media Center HITL Payout Gateway",
        audit_sink=resolved_tenant_audit_sink,
    )
    app.state.hitl_payout_api = HITLPayoutAPIState(
        queue_manager=queue_manager,
        veto_manager=veto_manager,
        confirmation_manager=confirmation_manager,
        execution_manager=execution_manager,
        publisher=resolved_publisher,
        audit_log_sink=resolved_audit_log_sink,
        tenant_audit_sink=resolved_tenant_audit_sink,
        totp_secrets=dict(totp_secrets or {}),
    )
    app.add_exception_handler(TenantCoreError, _tenant_core_error_handler)
    app.add_exception_handler(SharedError, _shared_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(PayoutNotFoundError, _payout_not_found_handler)
    app.add_exception_handler(VetoWindowClosedError, _veto_window_closed_handler)
    app.add_exception_handler(PayoutConnectorError, _payout_connector_error_handler)
    app.add_exception_handler(
        PayoutNotExecutableError,
        _payout_not_executable_handler,
    )
    app.add_exception_handler(PayoutQueueError, _payout_queue_error_handler)
    app.add_exception_handler(ValueError, _value_error_handler)
    app.include_router(router)
    return app


@router.post(
    "/payouts/queue",
    response_model=PayoutQueueItem,
    status_code=http_status.HTTP_201_CREATED,
    summary="Поставить выплату в очередь HITL",
)
async def queue_payout(
    payload: QueuePayoutRequest,
    state: Annotated[HITLPayoutAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> PayoutQueueItem:
    actor_context = require_access(PAYOUT_QUEUE_POLICY, context=context)
    result = await state.queue_manager.queue_payout(
        tenant_id=context.tenant_id,
        member_id=payload.member_id,
        period=payload.period,
        payout_share=payload.payout_share,
        distribution_id=payload.distribution_id,
        distribution_hash=payload.distribution_hash,
        created_by=payload.created_by or _subject(actor_context),
        correlation_id=_correlation_id(context),
        payout_id=payload.payout_id,
        event_id=payload.event_id,
        now=payload.now,
        requires_2fa=payload.requires_2fa,
        metadata=payload.metadata,
    )
    return result.payout


@router.get(
    "/payouts",
    response_model=PayoutListResponse,
    summary="Получить выплаты tenant с фильтром по статусу",
)
def list_payouts(
    state: Annotated[HITLPayoutAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
    status_filter: PayoutStatusQuery = None,
) -> PayoutListResponse:
    require_access(PAYOUT_READ_POLICY, context=context)
    return PayoutListResponse(
        items=state.queue_manager.list_payouts(
            tenant_id=context.tenant_id,
            status=status_filter,
        )
    )


@router.get(
    "/payouts/{payout_id}",
    response_model=PayoutQueueItem,
    summary="Получить выплату tenant",
)
def get_payout(
    payout_id: PayoutIdPath,
    state: Annotated[HITLPayoutAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> PayoutQueueItem:
    require_access(PAYOUT_READ_POLICY, context=context)
    return state.queue_manager.get_payout(
        tenant_id=context.tenant_id,
        payout_id=payout_id,
    )


@router.post(
    "/payouts/{payout_id}/veto",
    response_model=VetoDecision,
    summary="Наложить вето Совета на выплату",
)
async def veto_payout(
    payout_id: PayoutIdPath,
    payload: VetoPayoutRequest,
    state: Annotated[HITLPayoutAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> VetoDecision:
    actor_context = require_access(PAYOUT_VETO_POLICY, context=context)
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
    "/payouts/{payout_id}/confirm",
    response_model=PayoutConfirmation,
    summary="Подтвердить выплату через 2FA",
)
async def confirm_payout(
    payout_id: PayoutIdPath,
    payload: ConfirmPayoutRequest,
    state: Annotated[HITLPayoutAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> PayoutConfirmation:
    actor_context = require_access(PAYOUT_CONFIRM_POLICY, context=context)
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


@router.post(
    "/payouts/{payout_id}/execute",
    response_model=PayoutExecutionReceipt,
    summary="Исполнить подтверждённую выплату после окна вето",
)
async def execute_payout(
    payout_id: PayoutIdPath,
    payload: ExecutePayoutRequest,
    state: Annotated[HITLPayoutAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> PayoutExecutionReceipt:
    require_access(PAYOUT_EXECUTE_POLICY, context=context)
    return await state.execution_manager.execute_payout(
        tenant_id=context.tenant_id,
        payout_id=payout_id,
        correlation_id=_correlation_id(context),
        execution_id=payload.execution_id,
        event_id=payload.event_id,
        failure_event_id=payload.failure_event_id,
        notification_id=payload.notification_id,
        now=payload.now,
        metadata=payload.metadata,
    )


@router.post(
    "/payouts/{payout_id}/sync-status",
    response_model=PayoutPaymentStatusReceipt,
    summary="Сверить статус выплаты во внешнем платёжном шлюзе",
)
async def sync_payout_status(
    payout_id: PayoutIdPath,
    payload: SyncPayoutStatusRequest,
    state: Annotated[HITLPayoutAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> PayoutPaymentStatusReceipt:
    require_access(PAYOUT_SYNC_STATUS_POLICY, context=context)
    return await state.execution_manager.sync_payment_status(
        tenant_id=context.tenant_id,
        payout_id=payout_id,
        correlation_id=_correlation_id(context),
        event_id=payload.event_id,
        failure_event_id=payload.failure_event_id,
        now=payload.now,
        metadata=payload.metadata,
    )


def _api_state(request: Request) -> HITLPayoutAPIState:
    return cast(HITLPayoutAPIState, request.app.state.hitl_payout_api)


def _tenant_context() -> TenantContext:
    return require_tenant_context()


def _subject(context: TenantContext) -> SubjectId:
    if context.subject is None or context.subject.strip() == "":
        raise SharedError(
            status_code=400,
            error_code="actor_required",
            message="Операция HITL требует subject в tenant context",
            correlation_id=context.correlation_id,
        )

    return context.subject


def _totp_secret(state: HITLPayoutAPIState, context: TenantContext) -> str:
    subject = _subject(context)
    secret = state.totp_secrets.get((context.tenant_id, subject))
    if secret is None:
        raise SharedError(
            status_code=400,
            error_code="two_factor_secret_not_configured",
            message="2FA secret не настроен для subject в tenant",
            correlation_id=context.correlation_id,
        )

    return secret


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


async def _payout_connector_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    error = cast(PayoutConnectorError, exc)
    return JSONResponse(
        status_code=502,
        content=error_response_body(
            code="payout_connector_failed",
            message=str(error),
            details={
                "connector": error.connector_name,
                "error_code": error.error_code,
                "retryable": error.retryable,
            },
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


async def _value_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=error_response_body(
            code=VALIDATION_ERROR_CODE,
            message=str(exc),
        ),
    )
