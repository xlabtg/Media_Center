from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import inspect
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Protocol, Self
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import ConfigDict, Field, SecretStr, field_validator

from libs.shared.audit_logger import AuditLogger
from libs.shared.events import EventEnvelope, EventPublisher, InMemoryEventBus
from libs.shared.models import (
    AuditHash,
    CorrelationId,
    IdempotencyKey,
    JSONValue,
    SharedBaseModel,
    TenantId,
)
from libs.shared.tenant import TenantIsolationError

MESSENGER_ADAPTER_SOURCE = "messenger-adapter"
MESSENGER_ADAPTER_SCHEMA_VERSION = "1.0"
PUBLICATION_SUCCEEDED_EVENT = "publication.succeeded"
PUBLICATION_FAILED_EVENT = "publication.failed"

AES_256_KEY_BYTES = 32
AES_GCM_NONCE_BYTES = 12
TOKEN_CIPHERTEXT_PREFIX = "aes256gcm"

_PLATFORM_PATTERN = r"^[a-z][a-z0-9_-]{1,63}$"
_CONNECTOR_NAME_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"
_REF_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"
_TOKEN_CIPHERTEXT_PATTERN = r"^aes256gcm:[A-Za-z0-9_-]+={0,2}$"

PlatformName = Annotated[
    str,
    Field(
        min_length=2,
        max_length=64,
        pattern=_PLATFORM_PATTERN,
    ),
]
TargetId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=256,
    ),
]
DelaySleeper = Callable[[float], Awaitable[None] | None]


class PlatformTokenCryptoError(ValueError):
    """Raised when a platform token cannot be encrypted or decrypted."""


class PlatformTokenNotFoundError(LookupError):
    """Raised when no token is registered for the requested tenant/platform."""


class PlatformPublicationError(RuntimeError):
    """Publication failure with retry metadata understood by base adapters."""

    def __init__(
        self,
        message: str,
        *,
        platform: str,
        error_code: str = "publication_failed",
        retryable: bool = True,
        attempt_count: int = 0,
        audit_hash: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        if retry_after_seconds is not None and retry_after_seconds < 0:
            raise ValueError("retry_after_seconds не может быть отрицательным")
        self.platform = platform
        self.error_code = error_code
        self.retryable = retryable
        self.attempt_count = attempt_count
        self.audit_hash = audit_hash
        self.retry_after_seconds = retry_after_seconds

    def with_context(
        self,
        *,
        attempt_count: int,
        audit_hash: str | None = None,
    ) -> Self:
        self.attempt_count = attempt_count
        self.audit_hash = audit_hash
        return self


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_delay_seconds: float = 1.0
    multiplier: float = 2.0
    max_delay_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts должен быть не меньше 1")
        if self.initial_delay_seconds < 0:
            raise ValueError("initial_delay_seconds не может быть отрицательным")
        if self.multiplier < 1:
            raise ValueError("multiplier должен быть не меньше 1")
        if self.max_delay_seconds < 0:
            raise ValueError("max_delay_seconds не может быть отрицательным")

    def should_retry_after(self, attempt: int) -> bool:
        return attempt < self.max_attempts

    def delay_after(self, attempt: int) -> float:
        if attempt < 1:
            raise ValueError("attempt должен быть не меньше 1")
        delay = self.initial_delay_seconds * (self.multiplier ** (attempt - 1))
        return min(delay, self.max_delay_seconds)


class PublicationRequest(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    tenant_id: TenantId
    platform: PlatformName
    publication_id: IdempotencyKey
    target_id: TargetId
    content: str = Field(min_length=1, max_length=100_000)
    correlation_id: CorrelationId
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("platform", mode="before")
    @classmethod
    def _normalize_platform(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value


class PlatformPublishCommand(PublicationRequest):
    access_token: SecretStr
    attempt: int = Field(ge=1)
    requested_at: datetime

    @field_validator("requested_at")
    @classmethod
    def _normalize_requested_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class PlatformPublishResult(SharedBaseModel):
    platform: PlatformName
    platform_ref: str = Field(min_length=1, max_length=512)
    connector_name: str = Field(
        default="in_memory_platform",
        pattern=_CONNECTOR_NAME_PATTERN,
    )
    published_at: datetime

    @field_validator("platform", mode="before")
    @classmethod
    def _normalize_platform(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("published_at")
    @classmethod
    def _normalize_published_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class PublicationReceipt(SharedBaseModel):
    tenant_id: TenantId
    platform: PlatformName
    publication_id: IdempotencyKey
    target_id: TargetId
    platform_ref_hash: str = Field(pattern=_REF_HASH_PATTERN)
    attempt_count: int = Field(ge=1)
    audit_hash: AuditHash
    published_at: datetime
    correlation_id: CorrelationId

    @field_validator("published_at")
    @classmethod
    def _normalize_published_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class EncryptedPlatformToken(SharedBaseModel):
    tenant_id: TenantId
    platform: PlatformName
    token_id: IdempotencyKey
    token_encrypted: str = Field(pattern=_TOKEN_CIPHERTEXT_PATTERN)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class PlatformPublisher(Protocol):
    async def publish(
        self,
        command: PlatformPublishCommand,
    ) -> PlatformPublishResult:
        """Publish content through a concrete platform connector."""


class PlatformTokenRepository(Protocol):
    def require_token(
        self,
        *,
        tenant_id: str,
        platform: str,
        token_id: str | None = None,
    ) -> EncryptedPlatformToken:
        """Return an encrypted token owned by tenant_id."""

    def decrypt_token(
        self,
        record: EncryptedPlatformToken,
        *,
        tenant_id: str,
    ) -> SecretStr:
        """Decrypt a token only for the tenant that owns it."""


class PlatformTokenCipher:
    def __init__(self, encryption_key: str | bytes) -> None:
        self._key = _decode_aes256_key(encryption_key)
        self._aesgcm = AESGCM(self._key)

    def encrypt(
        self,
        *,
        tenant_id: str,
        platform: str,
        token: str,
    ) -> str:
        if token.strip() == "":
            raise PlatformTokenCryptoError("token не может быть пустым")

        nonce = os.urandom(AES_GCM_NONCE_BYTES)
        encrypted = self._aesgcm.encrypt(
            nonce,
            token.encode("utf-8"),
            _token_aad(tenant_id=tenant_id, platform=platform),
        )
        payload = base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")
        return f"{TOKEN_CIPHERTEXT_PREFIX}:{payload}"

    def decrypt(
        self,
        *,
        tenant_id: str,
        platform: str,
        token_encrypted: str,
    ) -> str:
        prefix, separator, encoded = token_encrypted.partition(":")
        if separator == "" or prefix != TOKEN_CIPHERTEXT_PREFIX:
            raise PlatformTokenCryptoError("неподдерживаемый формат token_encrypted")

        try:
            payload = base64.urlsafe_b64decode(encoded.encode("ascii"))
        except (binascii.Error, UnicodeEncodeError) as error:
            raise PlatformTokenCryptoError("token_encrypted повреждён") from error

        if len(payload) <= AES_GCM_NONCE_BYTES:
            raise PlatformTokenCryptoError("token_encrypted слишком короткий")

        nonce = payload[:AES_GCM_NONCE_BYTES]
        encrypted = payload[AES_GCM_NONCE_BYTES:]
        try:
            decrypted = self._aesgcm.decrypt(
                nonce,
                encrypted,
                _token_aad(tenant_id=tenant_id, platform=platform),
            )
        except InvalidTag as error:
            raise PlatformTokenCryptoError(
                "token_encrypted не прошёл проверку"
            ) from error

        return decrypted.decode("utf-8")


@dataclass(slots=True)
class InMemoryPlatformTokenStore:
    cipher: PlatformTokenCipher
    _tokens: dict[tuple[str, str, str], EncryptedPlatformToken] = field(
        default_factory=dict,
        init=False,
    )
    _primary_token_ids: dict[tuple[str, str], str] = field(
        default_factory=dict,
        init=False,
    )

    def save_token(
        self,
        *,
        tenant_id: str,
        platform: str,
        token: str,
        token_id: str = "primary",
        created_at: datetime | str | None = None,
    ) -> EncryptedPlatformToken:
        normalized_platform = _normalize_platform(platform)
        normalized_created_at = _normalize_datetime(created_at or datetime.now(UTC))
        record = EncryptedPlatformToken(
            tenant_id=tenant_id,
            platform=normalized_platform,
            token_id=token_id,
            token_encrypted=self.cipher.encrypt(
                tenant_id=tenant_id,
                platform=normalized_platform,
                token=token,
            ),
            created_at=normalized_created_at,
        )
        key = (tenant_id, normalized_platform, token_id)
        self._tokens[key] = record
        self._primary_token_ids[(tenant_id, normalized_platform)] = token_id
        return record

    def require_token(
        self,
        *,
        tenant_id: str,
        platform: str,
        token_id: str | None = None,
    ) -> EncryptedPlatformToken:
        normalized_platform = _normalize_platform(platform)
        resolved_token_id = token_id or self._primary_token_ids.get(
            (tenant_id, normalized_platform)
        )
        if resolved_token_id is None:
            raise PlatformTokenNotFoundError(
                "Токен площадки для tenant/platform не найден"
            )

        record = self._tokens.get((tenant_id, normalized_platform, resolved_token_id))
        if record is None:
            raise PlatformTokenNotFoundError(
                "Токен площадки для tenant/platform не найден"
            )

        return record

    def decrypt_token(
        self,
        record: EncryptedPlatformToken,
        *,
        tenant_id: str,
    ) -> SecretStr:
        if record.tenant_id != tenant_id:
            raise TenantIsolationError(
                "Токен площадки принадлежит другому tenant",
                details={
                    "resource_type": "platform_token",
                    "platform": record.platform,
                },
            )

        return SecretStr(
            self.cipher.decrypt(
                tenant_id=tenant_id,
                platform=record.platform,
                token_encrypted=record.token_encrypted,
            )
        )


@dataclass(slots=True)
class InMemoryPlatformPublisher:
    connector_name: str = "in_memory_platform"
    fail_with: PlatformPublicationError | None = None
    _commands: list[PlatformPublishCommand] = field(default_factory=list, init=False)

    @property
    def commands(self) -> tuple[PlatformPublishCommand, ...]:
        return tuple(self._commands)

    async def publish(
        self,
        command: PlatformPublishCommand,
    ) -> PlatformPublishResult:
        if self.fail_with is not None:
            raise self.fail_with

        self._commands.append(command)
        return PlatformPublishResult(
            platform=command.platform,
            platform_ref=f"{command.platform}-{command.publication_id}",
            connector_name=self.connector_name,
            published_at=command.requested_at,
        )


async def _default_sleep(delay_seconds: float) -> None:
    await asyncio.sleep(delay_seconds)


@dataclass(slots=True)
class BasePlatformAdapter:
    platform: str
    publisher: PlatformPublisher
    token_store: PlatformTokenRepository
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    event_publisher: EventPublisher = field(default_factory=InMemoryEventBus)
    audit_logger: AuditLogger = field(default_factory=AuditLogger)
    sleeper: DelaySleeper = _default_sleep
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    def __post_init__(self) -> None:
        self.platform = _normalize_platform(self.platform)

    async def publish(
        self,
        request: PublicationRequest,
        *,
        token_id: str | None = None,
        event_id: str | None = None,
        failure_event_id: str | None = None,
        now: datetime | str | None = None,
    ) -> PublicationReceipt:
        if request.platform != self.platform:
            raise PlatformPublicationError(
                "Площадка запроса не совпадает с базовым адаптером",
                platform=request.platform,
                error_code="platform_mismatch",
                retryable=False,
            )

        requested_at = _normalize_datetime(now or datetime.now(UTC))
        token_record = self.token_store.require_token(
            tenant_id=request.tenant_id,
            platform=request.platform,
            token_id=token_id,
        )
        access_token = self.token_store.decrypt_token(
            token_record,
            tenant_id=request.tenant_id,
        )

        last_error: PlatformPublicationError | None = None
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            try:
                platform_result = await self.publisher.publish(
                    PlatformPublishCommand(
                        **request.model_dump(mode="python"),
                        access_token=access_token,
                        attempt=attempt,
                        requested_at=requested_at,
                    )
                )
            except Exception as error:
                publication_error = _as_publication_error(
                    error,
                    platform=request.platform,
                ).with_context(attempt_count=attempt)
                last_error = publication_error
                if (
                    not publication_error.retryable
                    or not self.retry_policy.should_retry_after(attempt)
                ):
                    failure_audit_hash = await self._record_failure(
                        request=request,
                        error=publication_error,
                        failure_event_id=failure_event_id,
                    )
                    raise publication_error.with_context(
                        attempt_count=attempt,
                        audit_hash=failure_audit_hash,
                    ) from error

                delay = max(
                    self.retry_policy.delay_after(attempt),
                    publication_error.retry_after_seconds or 0,
                )
                self.logger.warning(
                    "Сбой публикации, будет повтор по retry policy",
                    extra={
                        "tenant_id": request.tenant_id,
                        "publication_id": request.publication_id,
                        "platform": request.platform,
                        "error_code": publication_error.error_code,
                        "attempt": attempt,
                        "delay_seconds": delay,
                        "retry_after_seconds": publication_error.retry_after_seconds,
                        "correlation_id": request.correlation_id,
                    },
                )
                await self._sleep(delay)
                continue

            return await self._record_success(
                request=request,
                platform_result=platform_result,
                attempt_count=attempt,
                event_id=event_id,
            )

        if last_error is not None:
            raise last_error
        raise PlatformPublicationError(
            "Публикация не была выполнена",
            platform=request.platform,
            error_code="publication_not_attempted",
            retryable=False,
        )

    async def _sleep(self, delay_seconds: float) -> None:
        sleep_result = self.sleeper(delay_seconds)
        if inspect.isawaitable(sleep_result):
            await sleep_result

    async def _record_success(
        self,
        *,
        request: PublicationRequest,
        platform_result: PlatformPublishResult,
        attempt_count: int,
        event_id: str | None,
    ) -> PublicationReceipt:
        platform_ref_hash = _hash_ref(
            tenant_id=request.tenant_id,
            value=f"{request.platform}:{platform_result.platform_ref}",
        )
        audit_record = self.audit_logger.record(
            event_type=PUBLICATION_SUCCEEDED_EVENT,
            tenant_id=request.tenant_id,
            metadata={
                "publication_id": request.publication_id,
                "platform": request.platform,
                "target_id": request.target_id,
                "platform_ref_hash": platform_ref_hash,
                "connector": platform_result.connector_name,
                "attempt_count": attempt_count,
                "metadata": request.metadata,
            },
            timestamp=platform_result.published_at,
            correlation_id=request.correlation_id,
            source=MESSENGER_ADAPTER_SOURCE,
        )
        event = EventEnvelope(
            event_id=event_id or _new_id("evt-publication-succeeded"),
            type=PUBLICATION_SUCCEEDED_EVENT,
            schema_version=MESSENGER_ADAPTER_SCHEMA_VERSION,
            tenant_id=request.tenant_id,
            source=MESSENGER_ADAPTER_SOURCE,
            correlation_id=request.correlation_id,
            occurred_at=platform_result.published_at,
            payload={
                "publication_id": request.publication_id,
                "platform": request.platform,
                "target_id": request.target_id,
                "platform_ref_hash": platform_ref_hash,
                "attempt_count": attempt_count,
                "audit_hash": audit_record.audit_hash,
            },
        )
        await self.event_publisher.publish(event)
        return PublicationReceipt(
            tenant_id=request.tenant_id,
            platform=request.platform,
            publication_id=request.publication_id,
            target_id=request.target_id,
            platform_ref_hash=platform_ref_hash,
            attempt_count=attempt_count,
            audit_hash=audit_record.audit_hash,
            published_at=platform_result.published_at,
            correlation_id=request.correlation_id,
        )

    async def _record_failure(
        self,
        *,
        request: PublicationRequest,
        error: PlatformPublicationError,
        failure_event_id: str | None,
    ) -> str:
        audit_record = self.audit_logger.record(
            event_type=PUBLICATION_FAILED_EVENT,
            tenant_id=request.tenant_id,
            metadata={
                "publication_id": request.publication_id,
                "platform": request.platform,
                "target_id": request.target_id,
                "error_code": error.error_code,
                "retryable": error.retryable,
                "attempt_count": error.attempt_count,
                "metadata": request.metadata,
            },
            timestamp=datetime.now(UTC),
            correlation_id=request.correlation_id,
            source=MESSENGER_ADAPTER_SOURCE,
        )
        self.logger.warning(
            "Публикация завершилась ошибкой",
            extra={
                "tenant_id": request.tenant_id,
                "publication_id": request.publication_id,
                "platform": request.platform,
                "error_code": error.error_code,
                "retryable": error.retryable,
                "attempt_count": error.attempt_count,
                "correlation_id": request.correlation_id,
            },
        )
        event = EventEnvelope(
            event_id=failure_event_id or _new_id("evt-publication-failed"),
            type=PUBLICATION_FAILED_EVENT,
            schema_version=MESSENGER_ADAPTER_SCHEMA_VERSION,
            tenant_id=request.tenant_id,
            source=MESSENGER_ADAPTER_SOURCE,
            correlation_id=request.correlation_id,
            occurred_at=audit_record.timestamp,
            payload={
                "publication_id": request.publication_id,
                "platform": request.platform,
                "target_id": request.target_id,
                "error_code": error.error_code,
                "retryable": error.retryable,
                "attempt_count": error.attempt_count,
                "audit_hash": audit_record.audit_hash,
            },
        )
        await self.event_publisher.publish(event)
        return audit_record.audit_hash


def _decode_aes256_key(encryption_key: str | bytes) -> bytes:
    if isinstance(encryption_key, bytes):
        if len(encryption_key) == AES_256_KEY_BYTES:
            return encryption_key
        raise PlatformTokenCryptoError("AES-256 ключ должен быть длиной 32 байта")

    value = encryption_key.strip()
    raw_value = value.encode("utf-8")
    if len(raw_value) == AES_256_KEY_BYTES:
        return raw_value

    try:
        decoded = base64.b64decode(value, validate=True)
    except binascii.Error:
        decoded = b""
    if len(decoded) == AES_256_KEY_BYTES:
        return decoded

    try:
        decoded_hex = bytes.fromhex(value)
    except ValueError:
        decoded_hex = b""
    if len(decoded_hex) == AES_256_KEY_BYTES:
        return decoded_hex

    raise PlatformTokenCryptoError("AES-256 ключ должен быть 32 байта, base64 или hex")


def _token_aad(*, tenant_id: str, platform: str) -> bytes:
    return f"{tenant_id}:{_normalize_platform(platform)}".encode()


def _as_publication_error(
    error: Exception,
    *,
    platform: str,
) -> PlatformPublicationError:
    if isinstance(error, PlatformPublicationError):
        return error

    return PlatformPublicationError(
        "Сбой публикации на площадку",
        platform=platform,
        error_code="publication_failed",
        retryable=True,
    )


def _hash_ref(*, tenant_id: str, value: str) -> str:
    return "sha256:" + hashlib.sha256(f"{tenant_id}:{value}".encode()).hexdigest()


def _normalize_platform(platform: str) -> str:
    normalized = platform.strip().lower()
    if normalized == "":
        raise ValueError("platform не может быть пустой")
    return normalized


def _normalize_datetime(value: datetime | str) -> datetime:
    if isinstance(value, str):
        normalized = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        normalized = value

    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=UTC)

    return normalized.astimezone(UTC)


def _new_id(prefix: str) -> IdempotencyKey:
    return f"{prefix}-{uuid4()}"
