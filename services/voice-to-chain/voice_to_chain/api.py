from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, cast
from uuid import uuid4

from blockchain_auditor import (
    AuditBatchError,
    AuditMetadataPolicyError,
    AuditRecordConflictError,
    GrpcBlockchainAuditConnector,
    GrpcBlockchainAuditTransport,
    InMemoryGrpcBlockchainAuditTransport,
    build_blockchain_auditor_settings,
)
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi import status as http_status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator

from libs.shared import (
    BOARD_ROLE,
    COUNCIL_ROLE,
    MEMBER_ASSOC_ROLE,
    MEMBER_FULL_ROLE,
    VALIDATION_ERROR_CODE,
    AccessPolicy,
    InMemoryAuditLogSink,
    InMemoryAuditSink,
    JSONValue,
    ServiceTemplateConfig,
    SharedBaseModel,
    SharedError,
    TenantContext,
    TenantCoreError,
    create_service_app,
    error_response_body,
    require_access,
    require_tenant_context,
)

from .retention import AudioDeletionReceipt, AudioRetentionError, normalize_datetime
from .service import (
    VoiceClock,
    VoiceToChainError,
    VoiceToChainService,
    VoiceTranscriptionCommand,
    VoiceTranscriptionReceipt,
)
from .settings import VoiceToChainSettings, build_voice_to_chain_settings
from .transcription import (
    WhisperCppCliConfig,
    WhisperCppCliTranscriber,
    WhisperCppTranscriber,
    WhisperCppTranscriptionError,
)

VOICE_TRANSCRIBE_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    BOARD_ROLE,
    MEMBER_FULL_ROLE,
    MEMBER_ASSOC_ROLE,
    action="voice.transcribe",
    resource_type="voice_to_chain",
)
VOICE_RETENTION_CLEANUP_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    BOARD_ROLE,
    action="voice.retention.cleanup",
    resource_type="voice_to_chain",
)

_CONTENT_TYPE_PATTERN = r"^audio/[A-Za-z0-9.+-]{1,64}$"
_LANGUAGE_PATTERN = r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$"


class VoiceTranscriptionRequest(SharedBaseModel):
    audio_id: str | None = Field(default=None, min_length=1, max_length=128)
    event_id: str | None = Field(default=None, min_length=1, max_length=128)
    content_type: str = Field(pattern=_CONTENT_TYPE_PATTERN)
    audio_base64: str = Field(min_length=1, max_length=20_000_000)
    language: str | None = Field(
        default=None,
        min_length=2,
        max_length=16,
        pattern=_LANGUAGE_PATTERN,
    )
    captured_at: datetime | None = None

    @field_validator("captured_at")
    @classmethod
    def _normalize_captured_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return normalize_datetime(value)


class VoiceRetentionCleanupRequest(SharedBaseModel):
    now: datetime | None = None

    @field_validator("now")
    @classmethod
    def _normalize_now(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return normalize_datetime(value)


class VoiceRetentionCleanupResponse(SharedBaseModel):
    deleted_count: int = Field(ge=0)
    items: tuple[AudioDeletionReceipt, ...]


@dataclass(slots=True)
class VoiceToChainAPIState:
    service: VoiceToChainService
    tenant_audit_sink: InMemoryAuditSink
    audit_log_sink: InMemoryAuditLogSink


router = APIRouter(tags=["Voice-to-Chain"])


def create_voice_to_chain_app(
    config: ServiceTemplateConfig,
    *,
    voice_settings: VoiceToChainSettings | None = None,
    transcriber: WhisperCppTranscriber | None = None,
    blockchain_connector: GrpcBlockchainAuditConnector | None = None,
    blockchain_transport: GrpcBlockchainAuditTransport | None = None,
    tenant_audit_sink: InMemoryAuditSink | None = None,
    audit_log_sink: InMemoryAuditLogSink | None = None,
    clock: VoiceClock | None = None,
) -> FastAPI:
    resolved_settings = voice_settings or build_voice_to_chain_settings()
    resolved_tenant_audit_sink = tenant_audit_sink or InMemoryAuditSink()
    resolved_audit_log_sink = audit_log_sink or InMemoryAuditLogSink()
    resolved_connector = blockchain_connector or GrpcBlockchainAuditConnector(
        settings=build_blockchain_auditor_settings(),
        transport=blockchain_transport or InMemoryGrpcBlockchainAuditTransport(),
    )
    resolved_transcriber = transcriber or WhisperCppCliTranscriber(
        WhisperCppCliConfig(
            binary_path=resolved_settings.whisper_cpp_binary_path,
            model_path=resolved_settings.whisper_cpp_model_path,
            default_language=resolved_settings.whisper_cpp_language,
            timeout_seconds=resolved_settings.whisper_cpp_timeout_seconds,
        )
    )
    app = create_service_app(
        config,
        title="Media Center Voice-to-Chain",
        audit_sink=resolved_tenant_audit_sink,
    )
    app.state.voice_to_chain_api = VoiceToChainAPIState(
        service=VoiceToChainService(
            transcriber=resolved_transcriber,
            blockchain_connector=resolved_connector,
            raw_audio_ttl=resolved_settings.raw_audio_ttl,
            audit_log_sink=resolved_audit_log_sink,
            clock=clock or _utcnow,
        ),
        tenant_audit_sink=resolved_tenant_audit_sink,
        audit_log_sink=resolved_audit_log_sink,
    )
    app.add_exception_handler(TenantCoreError, _tenant_core_error_handler)
    app.add_exception_handler(SharedError, _shared_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
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


@router.post(
    "/voice/transcribe",
    response_model=VoiceTranscriptionReceipt,
    status_code=http_status.HTTP_201_CREATED,
    summary="Локально транскрибировать голос и записать hash-only audit record",
)
async def transcribe_voice(
    payload: VoiceTranscriptionRequest,
    state: Annotated[VoiceToChainAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> VoiceTranscriptionReceipt:
    actor_context = require_access(VOICE_TRANSCRIBE_POLICY, context=context)
    audio_id = payload.audio_id or f"audio-{uuid4().hex}"
    event_id = payload.event_id or f"evt-{audio_id}"
    return await state.service.transcribe(
        VoiceTranscriptionCommand(
            tenant_id=context.tenant_id,
            audio_id=audio_id,
            event_id=event_id,
            content_type=payload.content_type,
            audio_bytes=_decode_audio(payload.audio_base64),
            language=payload.language,
            received_at=None,
            metadata=_request_metadata(payload),
        ),
        actor_context=actor_context,
    )


@router.post(
    "/voice/retention/cleanup",
    response_model=VoiceRetentionCleanupResponse,
    summary="Удалить исходное аудио с истекшим TTL",
)
def cleanup_voice_retention(
    payload: VoiceRetentionCleanupRequest,
    state: Annotated[VoiceToChainAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> VoiceRetentionCleanupResponse:
    actor_context = require_access(VOICE_RETENTION_CLEANUP_POLICY, context=context)
    deleted = state.service.cleanup_expired_audio(
        tenant_id=context.tenant_id,
        now=payload.now,
        actor_context=actor_context,
    )
    return VoiceRetentionCleanupResponse(
        deleted_count=len(deleted),
        items=deleted,
    )


def _decode_audio(value: str) -> bytes:
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


def _request_metadata(payload: VoiceTranscriptionRequest) -> dict[str, JSONValue]:
    if payload.captured_at is None:
        return {}

    return {
        "captured_at": payload.captured_at.isoformat().replace("+00:00", "Z"),
    }


def _api_state(request: Request) -> VoiceToChainAPIState:
    return cast(VoiceToChainAPIState, request.app.state.voice_to_chain_api)


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


async def _voice_to_chain_error_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    return _error_response(
        request,
        status_code=400,
        code="voice_to_chain_error",
        message=str(exc),
    )


async def _audio_retention_error_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    return _error_response(
        request,
        status_code=409,
        code="voice_audio_retention_conflict",
        message=str(exc),
    )


async def _whisper_cpp_error_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    return _error_response(
        request,
        status_code=502,
        code="voice_transcription_failed",
        message=str(exc),
    )


async def _audit_metadata_policy_error_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    return _error_response(
        request,
        status_code=400,
        code="audit_metadata_policy_violation",
        message=str(exc),
    )


async def _audit_batch_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return _error_response(
        request,
        status_code=400,
        code="audit_batch_invalid",
        message=str(exc),
    )


async def _audit_record_conflict_error_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    return _error_response(
        request,
        status_code=409,
        code="audit_record_conflict",
        message=str(exc),
    )


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_response_body(
            code=code,
            message=message,
            correlation_id=request.headers.get("x-correlation-id"),
        ),
    )


def _utcnow() -> datetime:
    return datetime.now(UTC)
