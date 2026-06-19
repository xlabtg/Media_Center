from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, Field, field_validator

from libs.shared import (
    VALIDATION_ERROR_CODE,
    AuditHash,
    EventType,
    IdempotencyKey,
    InMemoryAuditSink,
    JSONValue,
    ServiceTemplateConfig,
    SharedBaseModel,
    SharedError,
    TenantContext,
    TenantCoreError,
    TenantId,
    create_service_app,
    error_response_body,
    require_tenant_context,
)

from .connector import (
    AuditMetadataPolicyError,
    AuditRecord,
    GrpcBlockchainAuditConnector,
    GrpcBlockchainAuditTransport,
    InMemoryGrpcBlockchainAuditTransport,
    validate_audit_metadata,
)
from .hash_generator import generate_event_hash
from .settings import BlockchainAuditorSettings, build_blockchain_auditor_settings

HashMismatchReason = Literal["hash_mismatch"]
EventMismatchReason = Literal["event_type_mismatch", "hash_mismatch"]

AuditHashQuery = Annotated[AuditHash, Query(alias="hash")]
EventIdQuery = Annotated[IdempotencyKey, Query(alias="event_id")]


class AuditEventVerificationRequest(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    event_id: IdempotencyKey
    event_type: EventType
    timestamp: datetime
    points: float | None = Field(default=None, allow_inf_nan=False)
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def _normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class AuditEventVerificationResponse(SharedBaseModel):
    tenant_id: TenantId
    event_id: IdempotencyKey
    event_type: EventType
    matched: bool
    recorded_hash: AuditHash
    calculated_hash: AuditHash
    algorithm: str = Field(pattern="^sha256$")
    block_ref: str = Field(min_length=1)
    connector_name: str = Field(min_length=1)
    recorded_at: datetime
    mismatch_reason: EventMismatchReason | None = None


class AuditHashVerificationResponse(SharedBaseModel):
    tenant_id: TenantId
    event_id: IdempotencyKey
    matched: bool
    recorded_hash: AuditHash
    requested_hash: AuditHash
    block_ref: str = Field(min_length=1)
    connector_name: str = Field(min_length=1)
    recorded_at: datetime
    mismatch_reason: HashMismatchReason | None = None


@dataclass(slots=True)
class BlockchainAuditorAPIState:
    connector: GrpcBlockchainAuditConnector
    tenant_audit_sink: InMemoryAuditSink


router = APIRouter(tags=["Private Blockchain Auditor"])


def create_blockchain_auditor_app(
    config: ServiceTemplateConfig,
    *,
    auditor_settings: BlockchainAuditorSettings | None = None,
    transport: GrpcBlockchainAuditTransport | None = None,
    connector: GrpcBlockchainAuditConnector | None = None,
    tenant_audit_sink: InMemoryAuditSink | None = None,
) -> FastAPI:
    resolved_audit_sink = tenant_audit_sink or InMemoryAuditSink()
    resolved_connector = connector or GrpcBlockchainAuditConnector(
        settings=auditor_settings or build_blockchain_auditor_settings(),
        transport=transport or InMemoryGrpcBlockchainAuditTransport(),
    )
    app = create_service_app(
        config,
        title="Media Center Private Blockchain Auditor",
        audit_sink=resolved_audit_sink,
    )
    app.state.blockchain_auditor_api = BlockchainAuditorAPIState(
        connector=resolved_connector,
        tenant_audit_sink=resolved_audit_sink,
    )
    app.add_exception_handler(TenantCoreError, _tenant_core_error_handler)
    app.add_exception_handler(SharedError, _shared_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(
        AuditMetadataPolicyError,
        _audit_metadata_policy_error_handler,
    )
    app.include_router(router)
    return app


@router.post(
    "/audit/verify",
    response_model=AuditEventVerificationResponse,
    summary="Проверить соответствие события audit record",
)
async def verify_audit_event(
    payload: AuditEventVerificationRequest,
    state: Annotated[BlockchainAuditorAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> AuditEventVerificationResponse:
    record = await _get_audit_record_or_404(
        state=state,
        context=context,
        event_id=payload.event_id,
    )
    generated = generate_event_hash(
        event_type=payload.event_type,
        tenant_id=context.tenant_id,
        timestamp=payload.timestamp,
        points=payload.points,
        metadata=validate_audit_metadata(payload.metadata),
    )
    mismatch_reason = _event_mismatch_reason(
        record=record,
        requested_event_type=payload.event_type,
        calculated_hash=generated.audit_hash,
    )
    return AuditEventVerificationResponse(
        tenant_id=context.tenant_id,
        event_id=payload.event_id,
        event_type=payload.event_type,
        matched=mismatch_reason is None,
        recorded_hash=record.audit_hash,
        calculated_hash=generated.audit_hash,
        algorithm=generated.algorithm,
        block_ref=record.block_ref,
        connector_name=record.connector_name,
        recorded_at=record.recorded_at,
        mismatch_reason=mismatch_reason,
    )


@router.get(
    "/audit/verify",
    response_model=AuditHashVerificationResponse,
    summary="Проверить записанный audit hash",
)
async def verify_audit_hash(
    event_id: EventIdQuery,
    audit_hash: AuditHashQuery,
    state: Annotated[BlockchainAuditorAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> AuditHashVerificationResponse:
    record = await _get_audit_record_or_404(
        state=state,
        context=context,
        event_id=event_id,
    )
    matched = record.audit_hash == audit_hash
    return AuditHashVerificationResponse(
        tenant_id=context.tenant_id,
        event_id=event_id,
        matched=matched,
        recorded_hash=record.audit_hash,
        requested_hash=audit_hash,
        block_ref=record.block_ref,
        connector_name=record.connector_name,
        recorded_at=record.recorded_at,
        mismatch_reason=None if matched else "hash_mismatch",
    )


async def _get_audit_record_or_404(
    *,
    state: BlockchainAuditorAPIState,
    context: TenantContext,
    event_id: str,
) -> AuditRecord:
    record = await state.connector.get_audit_record(
        tenant_id=context.tenant_id,
        event_id=event_id,
        context=context,
    )
    if record is None:
        raise SharedError(
            status_code=404,
            error_code="audit_record_not_found",
            message="Audit record не найден",
            correlation_id=context.correlation_id,
        )

    return record


def _event_mismatch_reason(
    *,
    record: AuditRecord,
    requested_event_type: str,
    calculated_hash: str,
) -> EventMismatchReason | None:
    if record.event_type != requested_event_type:
        return "event_type_mismatch"
    if record.audit_hash != calculated_hash:
        return "hash_mismatch"

    return None


def _api_state(request: Request) -> BlockchainAuditorAPIState:
    return cast(BlockchainAuditorAPIState, request.app.state.blockchain_auditor_api)


def _tenant_context() -> TenantContext:
    return require_tenant_context()


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


async def _audit_metadata_policy_error_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=error_response_body(
            code="audit_metadata_policy_violation",
            message=str(exc),
            correlation_id=request.headers.get("x-correlation-id"),
        ),
    )
