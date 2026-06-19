from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from blockchain_auditor import (
    AuditRecordCommand,
    AuditRecordReceipt,
    GrpcBlockchainAuditConnector,
    generate_event_hash,
)
from pydantic import Field, field_validator

from libs.shared import (
    COUNCIL_ROLE,
    AuditLogger,
    CorrelationId,
    IdempotencyKey,
    InMemoryAuditLogSink,
    JSONValue,
    SharedBaseModel,
    TenantContext,
    TenantId,
)

from .retention import (
    AudioDeletionReceipt,
    AudioRetentionStatus,
    InMemoryTemporaryAudioStore,
    TemporaryAudioRecord,
    normalize_datetime,
)
from .transcription import (
    AudioTranscriptionInput,
    WhisperCppTranscriber,
    WhisperCppTranscript,
)

VOICE_TRANSCRIPT_RECORDED_EVENT = "voice.transcript.recorded"
VOICE_AUDIO_DELETED_EVENT = "voice.audio.deleted"
VOICE_TO_CHAIN_SOURCE = "voice-to-chain"
VOICE_TO_CHAIN_SCHEMA_VERSION = "1.0"
MAX_RAW_AUDIO_TTL = timedelta(hours=24)

VoiceClock = Callable[[], datetime]


class VoiceToChainError(RuntimeError):
    """Base error for Voice-to-Chain service orchestration."""


@dataclass(frozen=True, slots=True)
class VoiceTranscriptionCommand:
    tenant_id: str
    audio_id: str
    event_id: str
    content_type: str
    audio_bytes: bytes
    language: str | None = None
    received_at: datetime | None = None
    metadata: dict[str, JSONValue] = field(default_factory=dict)


class VoiceTranscriptionReceipt(SharedBaseModel):
    tenant_id: TenantId
    audio_id: IdempotencyKey
    event_id: IdempotencyKey
    transcript: str = Field(min_length=1, max_length=200_000)
    language: str | None = Field(default=None, min_length=2, max_length=16)
    transcriber: str = Field(pattern=r"^whisper\.cpp$")
    model_name: str = Field(min_length=1, max_length=256)
    transcript_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    audio_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    audit_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    block_ref: str = Field(min_length=1, max_length=512)
    raw_audio_status: AudioRetentionStatus
    raw_audio_expires_at: datetime
    completed_at: datetime
    correlation_id: CorrelationId | None = None

    @field_validator("raw_audio_expires_at", "completed_at")
    @classmethod
    def _normalize_datetime(cls, value: datetime) -> datetime:
        return normalize_datetime(value)


@dataclass(slots=True)
class VoiceToChainService:
    transcriber: WhisperCppTranscriber
    blockchain_connector: GrpcBlockchainAuditConnector
    audio_store: InMemoryTemporaryAudioStore = field(
        default_factory=InMemoryTemporaryAudioStore,
    )
    raw_audio_ttl: timedelta = MAX_RAW_AUDIO_TTL
    audit_log_sink: InMemoryAuditLogSink = field(default_factory=InMemoryAuditLogSink)
    clock: VoiceClock = field(default_factory=lambda: _utcnow)
    blockchain_service_subject: str = "voice-to-chain-service"

    def __post_init__(self) -> None:
        if self.raw_audio_ttl <= timedelta(0):
            raise ValueError("raw_audio_ttl должен быть положительным")
        if self.raw_audio_ttl > MAX_RAW_AUDIO_TTL:
            raise ValueError("сырой голос должен удаляться не позднее 24 часов")

    async def transcribe(
        self,
        command: VoiceTranscriptionCommand,
        *,
        actor_context: TenantContext,
    ) -> VoiceTranscriptionReceipt:
        received_at = normalize_datetime(command.received_at or self.clock())
        expires_at = received_at + self.raw_audio_ttl
        audio_record = self.audio_store.put_audio(
            tenant_id=command.tenant_id,
            audio_id=command.audio_id,
            content_type=command.content_type,
            audio_bytes=command.audio_bytes,
            received_at=received_at,
            expires_at=expires_at,
        )
        transcript = await self.transcriber.transcribe(
            AudioTranscriptionInput(
                tenant_id=command.tenant_id,
                audio_id=command.audio_id,
                content_type=command.content_type,
                audio_sha256=audio_record.audio_sha256,
                audio_bytes=command.audio_bytes,
                language=command.language,
                metadata=command.metadata,
            )
        )
        transcript_sha256 = _hash_text(transcript.text)
        audit_metadata = _audit_metadata(
            audio_record=audio_record,
            transcript=transcript,
            transcript_sha256=transcript_sha256,
        )
        generated_hash = generate_event_hash(
            event_type=VOICE_TRANSCRIPT_RECORDED_EVENT,
            tenant_id=command.tenant_id,
            timestamp=transcript.completed_at,
            metadata=audit_metadata,
        )
        chain_receipt = await self.blockchain_connector.record_audit_hash(
            AuditRecordCommand(
                tenant_id=command.tenant_id,
                event_id=command.event_id,
                event_type=VOICE_TRANSCRIPT_RECORDED_EVENT,
                audit_hash=generated_hash.audit_hash,
                occurred_at=transcript.completed_at,
                correlation_id=actor_context.correlation_id,
                metadata=audit_metadata,
            ),
            context=_blockchain_service_context(
                actor_context,
                subject=self.blockchain_service_subject,
            ),
        )
        self._audit_logger().record(
            event_type=VOICE_TRANSCRIPT_RECORDED_EVENT,
            tenant_id=command.tenant_id,
            metadata=audit_metadata,
            timestamp=transcript.completed_at,
            correlation_id=actor_context.correlation_id,
            source=VOICE_TO_CHAIN_SOURCE,
        )

        return _transcription_receipt(
            tenant_id=command.tenant_id,
            audio_record=audio_record,
            event_id=command.event_id,
            transcript=transcript,
            transcript_sha256=transcript_sha256,
            audit_hash=generated_hash.audit_hash,
            chain_receipt=chain_receipt,
            correlation_id=actor_context.correlation_id,
        )

    def cleanup_expired_audio(
        self,
        *,
        tenant_id: str,
        now: datetime | None = None,
        actor_context: TenantContext,
    ) -> tuple[AudioDeletionReceipt, ...]:
        deleted = self.audio_store.delete_expired_audio(
            tenant_id=tenant_id,
            now=now or self.clock(),
        )
        for receipt in deleted:
            self._audit_logger().record(
                event_type=VOICE_AUDIO_DELETED_EVENT,
                tenant_id=receipt.tenant_id,
                metadata={
                    "source": VOICE_TO_CHAIN_SOURCE,
                    "schema_version": VOICE_TO_CHAIN_SCHEMA_VERSION,
                    "audio_id": receipt.audio_id,
                    "audio_sha256": receipt.audio_sha256,
                    "reason": receipt.reason,
                },
                timestamp=receipt.deleted_at,
                correlation_id=actor_context.correlation_id,
                source=VOICE_TO_CHAIN_SOURCE,
            )

        return deleted

    def _audit_logger(self) -> AuditLogger:
        return AuditLogger(sink=self.audit_log_sink)


def _transcription_receipt(
    *,
    tenant_id: str,
    audio_record: TemporaryAudioRecord,
    event_id: str,
    transcript: WhisperCppTranscript,
    transcript_sha256: str,
    audit_hash: str,
    chain_receipt: AuditRecordReceipt,
    correlation_id: str | None,
) -> VoiceTranscriptionReceipt:
    return VoiceTranscriptionReceipt(
        tenant_id=tenant_id,
        audio_id=audio_record.audio_id,
        event_id=event_id,
        transcript=transcript.text,
        language=transcript.language,
        transcriber=transcript.transcriber,
        model_name=transcript.model_name,
        transcript_sha256=transcript_sha256,
        audio_sha256=audio_record.audio_sha256,
        audit_hash=audit_hash,
        block_ref=chain_receipt.block_ref,
        raw_audio_status=audio_record.status,
        raw_audio_expires_at=audio_record.expires_at,
        completed_at=transcript.completed_at,
        correlation_id=correlation_id,
    )


def _audit_metadata(
    *,
    audio_record: TemporaryAudioRecord,
    transcript: WhisperCppTranscript,
    transcript_sha256: str,
) -> dict[str, JSONValue]:
    metadata: dict[str, JSONValue] = {
        "source": VOICE_TO_CHAIN_SOURCE,
        "schema_version": VOICE_TO_CHAIN_SCHEMA_VERSION,
        "audio_id": audio_record.audio_id,
        "audio_sha256": audio_record.audio_sha256,
        "transcript_sha256": transcript_sha256,
        "transcriber": transcript.transcriber,
        "model_name": transcript.model_name,
        "raw_audio_expires_at": _format_datetime(audio_record.expires_at),
    }
    if transcript.language is not None:
        metadata["language"] = transcript.language

    return metadata


def _blockchain_service_context(
    actor_context: TenantContext,
    *,
    subject: str,
) -> TenantContext:
    return TenantContext(
        tenant_id=actor_context.tenant_id,
        subject=subject,
        roles=(COUNCIL_ROLE,),
        correlation_id=actor_context.correlation_id,
    )


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _format_datetime(value: datetime) -> str:
    return normalize_datetime(value).isoformat().replace("+00:00", "Z")


def _utcnow() -> datetime:
    return datetime.now(UTC)
