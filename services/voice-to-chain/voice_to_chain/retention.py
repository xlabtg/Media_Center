from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, field_validator

from libs.shared.models import (
    IdempotencyKey,
    SharedBaseModel,
    TenantId,
)


class AudioRetentionError(RuntimeError):
    """Base error for temporary raw audio retention failures."""


class AudioRetentionStatus(StrEnum):
    PENDING_DELETION = "pending_deletion"
    DELETED = "deleted"


class TemporaryAudioRecord(SharedBaseModel):
    tenant_id: TenantId
    audio_id: IdempotencyKey
    content_type: str = Field(min_length=1, max_length=128)
    audio_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    received_at: datetime
    expires_at: datetime
    status: AudioRetentionStatus
    deleted_at: datetime | None = None

    @field_validator("received_at", "expires_at", "deleted_at")
    @classmethod
    def _normalize_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return normalize_datetime(value)


class AudioDeletionReceipt(SharedBaseModel):
    tenant_id: TenantId
    audio_id: IdempotencyKey
    audio_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    expires_at: datetime
    deleted_at: datetime
    reason: str = Field(default="ttl_expired", pattern=r"^[a-z][a-z0-9_]{0,63}$")

    @field_validator("expires_at", "deleted_at")
    @classmethod
    def _normalize_datetime(cls, value: datetime) -> datetime:
        return normalize_datetime(value)


@dataclass(slots=True)
class InMemoryTemporaryAudioStore:
    """Tenant-scoped temporary audio store for tests and local wiring."""

    _records: dict[tuple[str, str], TemporaryAudioRecord] = field(
        default_factory=dict,
    )
    _payloads: dict[tuple[str, str], bytes] = field(default_factory=dict)

    @property
    def records(self) -> tuple[TemporaryAudioRecord, ...]:
        return tuple(self._records.values())

    def put_audio(
        self,
        *,
        tenant_id: str,
        audio_id: str,
        content_type: str,
        audio_bytes: bytes,
        received_at: datetime,
        expires_at: datetime,
    ) -> TemporaryAudioRecord:
        key = tenant_id, audio_id
        existing = self._records.get(key)
        if (
            existing is not None
            and existing.status is AudioRetentionStatus.PENDING_DELETION
        ):
            raise AudioRetentionError("audio_id уже ожидает удаления")

        record = TemporaryAudioRecord(
            tenant_id=tenant_id,
            audio_id=audio_id,
            content_type=content_type,
            audio_sha256=hash_bytes(audio_bytes),
            received_at=received_at,
            expires_at=expires_at,
            status=AudioRetentionStatus.PENDING_DELETION,
        )
        self._records[key] = record
        self._payloads[key] = bytes(audio_bytes)
        return record

    def get_audio(
        self,
        *,
        tenant_id: str,
        audio_id: str,
    ) -> TemporaryAudioRecord | None:
        return self._records.get((tenant_id, audio_id))

    def read_audio_bytes(
        self,
        *,
        tenant_id: str,
        audio_id: str,
    ) -> bytes | None:
        return self._payloads.get((tenant_id, audio_id))

    def delete_expired_audio(
        self,
        *,
        now: datetime,
        tenant_id: str | None = None,
    ) -> tuple[AudioDeletionReceipt, ...]:
        normalized_now = normalize_datetime(now)
        receipts: list[AudioDeletionReceipt] = []
        for key, record in tuple(self._records.items()):
            if tenant_id is not None and record.tenant_id != tenant_id:
                continue
            if record.status is not AudioRetentionStatus.PENDING_DELETION:
                continue
            if record.expires_at > normalized_now:
                continue

            self._payloads.pop(key, None)
            deleted_record = record.model_copy(
                update={
                    "status": AudioRetentionStatus.DELETED,
                    "deleted_at": normalized_now,
                }
            )
            self._records[key] = deleted_record
            receipts.append(
                AudioDeletionReceipt(
                    tenant_id=record.tenant_id,
                    audio_id=record.audio_id,
                    audio_sha256=record.audio_sha256,
                    expires_at=record.expires_at,
                    deleted_at=normalized_now,
                )
            )

        return tuple(receipts)


def hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)
