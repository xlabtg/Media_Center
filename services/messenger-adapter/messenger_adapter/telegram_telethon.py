"""Telethon integration for the Unified Messenger Adapter (issue #75).

The module keeps the existing Bot API publisher intact and adds a separate
Telethon path for user-session based publishing and inbound interaction:

- encrypted tenant-scoped ``StringSession`` storage;
- a lazy Telethon client provider for production wiring;
- a ``PlatformPublisher`` implementation for publication through Telethon;
- a small inbound bridge that routes read messages into ``TelegramClientGateway``.

Tests use the same protocols with fake clients, so no real Telegram account or
network access is required for the acceptance contract.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast
from uuid import uuid4

from pydantic import Field, SecretStr, field_validator

from libs.shared.models import (
    AuditHash,
    CorrelationId,
    IdempotencyKey,
    JSONValue,
    SharedBaseModel,
    TenantId,
)
from libs.shared.tenant import TenantIsolationError
from messenger_adapter.base_adapter import (
    PlatformPublicationError,
    PlatformPublishCommand,
    PlatformPublishResult,
    PlatformTokenCipher,
)
from messenger_adapter.telegram_adapter import TELEGRAM_PLATFORM
from messenger_adapter.telegram_client import (
    Sha256Hash,
    TelegramAccountNotLinkedError,
    TelegramClientError,
    TelegramClientGateway,
    TelegramClientScenario,
    TelegramInboundMessage,
)

TELEGRAM_TELETHON_SESSION_AAD_LABEL = "telegram_telethon_session"
TELEGRAM_TELETHON_CONNECTOR_NAME = "telegram_telethon"

_TOKEN_CIPHERTEXT_PATTERN = r"^aes256gcm:[A-Za-z0-9_-]+={0,2}$"
_SHA256_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"

TelethonDelaySleeper = Callable[[float], Awaitable[None] | None]


class TelegramTelethonError(RuntimeError):
    """Base error for Telethon integration wiring."""


class TelegramTelethonSessionNotFoundError(TelegramTelethonError):
    """Raised when no encrypted Telethon session exists for tenant/ref."""


class TelegramTelethonAuthorizationError(TelegramTelethonError):
    """Raised when a stored Telethon session is not authorized."""


class TelegramTelethonSessionRecord(SharedBaseModel):
    """Encrypted tenant-scoped Telethon StringSession record."""

    tenant_id: TenantId
    session_ref: IdempotencyKey
    session_encrypted: str = Field(pattern=_TOKEN_CIPHERTEXT_PATTERN)
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("created_at", "updated_at")
    @classmethod
    def _normalize_datetime_field(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class TelegramTelethonSessionRepository(Protocol):
    def save_session(
        self,
        *,
        tenant_id: str,
        session_ref: str,
        session_string: str,
        metadata: Mapping[str, JSONValue] | None = None,
        saved_at: datetime | str | None = None,
    ) -> TelegramTelethonSessionRecord:
        """Encrypt and persist a tenant-owned Telethon StringSession."""

    def require_session(
        self,
        *,
        tenant_id: str,
        session_ref: str,
    ) -> TelegramTelethonSessionRecord:
        """Return an encrypted session for tenant/ref or raise."""

    def decrypt_session(
        self,
        record: TelegramTelethonSessionRecord,
        *,
        tenant_id: str,
    ) -> SecretStr:
        """Decrypt a session only for the tenant that owns it."""


@dataclass(slots=True)
class InMemoryTelegramTelethonSessionStore:
    """In-memory encrypted Telethon session store for tests and local wiring."""

    cipher: PlatformTokenCipher
    _sessions: dict[tuple[str, str], TelegramTelethonSessionRecord] = field(
        default_factory=dict,
        init=False,
    )

    def save_session(
        self,
        *,
        tenant_id: str,
        session_ref: str,
        session_string: str,
        metadata: Mapping[str, JSONValue] | None = None,
        saved_at: datetime | str | None = None,
    ) -> TelegramTelethonSessionRecord:
        if session_string.strip() == "":
            raise ValueError("Telethon session_string не может быть пустой")

        saved_at_dt = _normalize_datetime(saved_at or datetime.now(UTC))
        existing = self._sessions.get((tenant_id, session_ref))
        created_at = existing.created_at if existing is not None else saved_at_dt
        record = TelegramTelethonSessionRecord(
            tenant_id=tenant_id,
            session_ref=session_ref,
            session_encrypted=self.cipher.encrypt(
                tenant_id=tenant_id,
                platform=TELEGRAM_TELETHON_SESSION_AAD_LABEL,
                token=session_string,
            ),
            created_at=created_at,
            updated_at=saved_at_dt,
            metadata=dict(metadata or (existing.metadata if existing else {})),
        )
        self._sessions[(tenant_id, session_ref)] = record
        return record

    def require_session(
        self,
        *,
        tenant_id: str,
        session_ref: str,
    ) -> TelegramTelethonSessionRecord:
        record = self._sessions.get((tenant_id, session_ref))
        if record is None:
            raise TelegramTelethonSessionNotFoundError(
                "Telethon-сессия для tenant/session_ref не найдена"
            )

        return record

    def decrypt_session(
        self,
        record: TelegramTelethonSessionRecord,
        *,
        tenant_id: str,
    ) -> SecretStr:
        if record.tenant_id != tenant_id:
            raise TenantIsolationError(
                "Telethon-сессия принадлежит другому tenant",
                details={"resource_type": "telegram_telethon_session"},
            )

        return SecretStr(
            self.cipher.decrypt(
                tenant_id=tenant_id,
                platform=TELEGRAM_TELETHON_SESSION_AAD_LABEL,
                token_encrypted=record.session_encrypted,
            )
        )


@dataclass(frozen=True, slots=True)
class TelegramTelethonClientConfig:
    """Runtime-only config passed into a concrete Telethon client factory."""

    tenant_id: str
    session_ref: str
    session_string: str
    api_id: int
    api_hash: SecretStr
    proxy: object | None = None


class TelegramTelethonClient(Protocol):
    async def connect(self) -> None:
        """Open a Telethon connection."""

    async def disconnect(self) -> None:
        """Close a Telethon connection."""

    async def is_user_authorized(self) -> bool:
        """Return whether the loaded StringSession is authorized."""

    async def send_message(
        self,
        entity: object,
        message: str,
        **kwargs: object,
    ) -> object:
        """Send a message through Telethon and return its Message object."""

    def iter_messages(
        self,
        entity: object,
        **kwargs: object,
    ) -> AsyncIterator[object]:
        """Iterate over messages from a dialog/channel."""


class TelegramTelethonClientFactory(Protocol):
    def create_client(
        self,
        config: TelegramTelethonClientConfig,
    ) -> TelegramTelethonClient:
        """Build a Telethon-compatible client from runtime config."""


@dataclass(slots=True)
class TelethonClientFactory:
    """Production factory that imports Telethon only when it is used."""

    def create_client(
        self,
        config: TelegramTelethonClientConfig,
    ) -> TelegramTelethonClient:
        telethon_module = importlib.import_module("telethon")
        sessions_module = importlib.import_module("telethon.sessions")
        telegram_client_class = telethon_module.TelegramClient
        string_session_class = sessions_module.StringSession

        session = string_session_class(config.session_string)
        client = telegram_client_class(
            session,
            config.api_id,
            config.api_hash.get_secret_value(),
            proxy=config.proxy,
        )
        return cast(TelegramTelethonClient, client)


class TelegramTelethonClientProvider(Protocol):
    def connected(
        self,
        *,
        tenant_id: str,
        session_ref: str,
    ) -> AbstractAsyncContextManager[TelegramTelethonClient]:
        """Return an authorized Telethon client context for tenant/session_ref."""


@dataclass(slots=True)
class TelegramTelethonSessionClientProvider:
    """Decrypts a stored session, opens Telethon and re-saves rotated sessions."""

    session_store: TelegramTelethonSessionRepository
    api_id: int
    api_hash: SecretStr
    client_factory: TelegramTelethonClientFactory = field(
        default_factory=TelethonClientFactory
    )
    session_extractor: Callable[[TelegramTelethonClient], str | None] | None = None
    ensure_authorized: bool = True
    proxy: object | None = None
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    def __post_init__(self) -> None:
        if self.api_id <= 0:
            raise ValueError("api_id должен быть положительным")

    @asynccontextmanager
    async def connected(
        self,
        *,
        tenant_id: str,
        session_ref: str,
    ) -> AsyncIterator[TelegramTelethonClient]:
        record = self.session_store.require_session(
            tenant_id=tenant_id,
            session_ref=session_ref,
        )
        session_string = self.session_store.decrypt_session(
            record,
            tenant_id=tenant_id,
        ).get_secret_value()
        client = self.client_factory.create_client(
            TelegramTelethonClientConfig(
                tenant_id=tenant_id,
                session_ref=session_ref,
                session_string=session_string,
                api_id=self.api_id,
                api_hash=self.api_hash,
                proxy=self.proxy,
            )
        )

        await client.connect()
        try:
            if self.ensure_authorized and not await client.is_user_authorized():
                raise TelegramTelethonAuthorizationError(
                    "Telethon-сессия не авторизована"
                )

            yield client

            updated_session = self._extract_session(client)
            if updated_session is not None and updated_session != session_string:
                self.session_store.save_session(
                    tenant_id=tenant_id,
                    session_ref=session_ref,
                    session_string=updated_session,
                    metadata=record.metadata,
                )
        finally:
            await client.disconnect()

    def _extract_session(self, client: TelegramTelethonClient) -> str | None:
        if self.session_extractor is not None:
            return self.session_extractor(client)

        return telethon_string_session_from_client(client)


def telethon_string_session_from_client(
    client: TelegramTelethonClient,
) -> str | None:
    """Best-effort extraction of a rotated Telethon StringSession."""

    session = _attr(client, "session")
    if session is None:
        return None

    sessions_module = importlib.import_module("telethon.sessions")
    string_session_class = sessions_module.StringSession
    saved = string_session_class.save(session)
    if isinstance(saved, str) and saved.strip() != "":
        return saved

    return None


class TelegramTelethonRateLimit(SharedBaseModel):
    """Simple outbound pacing limits applied before Telethon send calls."""

    max_messages_per_minute: int = Field(default=60, ge=1, le=1200)
    min_interval_seconds: float = Field(default=1.0, ge=0, le=3600)

    @property
    def effective_interval_seconds(self) -> float:
        return max(self.min_interval_seconds, 60 / self.max_messages_per_minute)


@dataclass(slots=True)
class TelegramTelethonRateLimiter:
    limit: TelegramTelethonRateLimit = field(default_factory=TelegramTelethonRateLimit)
    sleeper: TelethonDelaySleeper = field(default_factory=lambda: _default_sleep)
    _last_sent_at: dict[tuple[str, str, str, str], datetime] = field(
        default_factory=dict,
        init=False,
    )

    async def acquire(
        self,
        *,
        tenant_id: str,
        session_ref: str,
        target_ref: str,
        action: str,
        now: datetime | str | None = None,
    ) -> float:
        requested_at = _normalize_datetime(now or datetime.now(UTC))
        key = (tenant_id, session_ref, target_ref, action)
        last_sent_at = self._last_sent_at.get(key)
        delay = 0.0
        if last_sent_at is not None:
            elapsed = (requested_at - last_sent_at).total_seconds()
            delay = max(0.0, self.limit.effective_interval_seconds - elapsed)

        if delay > 0:
            await _sleep(self.sleeper, delay)

        self._last_sent_at[key] = requested_at + timedelta(seconds=delay)
        return delay


@dataclass(slots=True)
class TelegramTelethonPublisher:
    """Publish text through a tenant-scoped Telethon user session."""

    client_provider: TelegramTelethonClientProvider
    rate_limiter: TelegramTelethonRateLimiter = field(
        default_factory=TelegramTelethonRateLimiter
    )
    connector_name: str = TELEGRAM_TELETHON_CONNECTOR_NAME

    async def publish(
        self,
        command: PlatformPublishCommand,
    ) -> PlatformPublishResult:
        if command.platform != TELEGRAM_PLATFORM:
            raise PlatformPublicationError(
                "Telethon publisher получил запрос другой площадки",
                platform=command.platform,
                error_code="platform_mismatch",
                retryable=False,
            )

        session_ref = _session_ref_from_command(command)
        try:
            async with self.client_provider.connected(
                tenant_id=command.tenant_id,
                session_ref=session_ref,
            ) as client:
                await self.rate_limiter.acquire(
                    tenant_id=command.tenant_id,
                    session_ref=session_ref,
                    target_ref=command.target_id,
                    action="publish",
                )
                message = await client.send_message(
                    command.target_id,
                    command.content,
                    **_telethon_send_options(command.metadata),
                )
        except Exception as error:
            raise _publication_error_from_telethon(
                error,
                platform=TELEGRAM_PLATFORM,
            ) from error

        message_id = _message_id(message)
        if message_id is None:
            raise PlatformPublicationError(
                "Telethon вернул сообщение без id",
                platform=TELEGRAM_PLATFORM,
                error_code="invalid_response",
                retryable=True,
            )

        return PlatformPublishResult(
            platform=TELEGRAM_PLATFORM,
            platform_ref=f"{_chat_ref(message, command.target_id)}:{message_id}",
            connector_name=self.connector_name,
            published_at=_message_datetime(message, fallback=command.requested_at),
        )


class TelegramTelethonPollRequest(SharedBaseModel):
    tenant_id: TenantId
    session_ref: IdempotencyKey
    source: str = Field(min_length=1, max_length=256)
    correlation_id: CorrelationId
    limit: int = Field(default=50, ge=1, le=100)
    offset_id: int | None = Field(default=None, ge=0)
    reply_to_messages: bool = True


class TelegramTelethonHandledUpdate(SharedBaseModel):
    message_id: int = Field(ge=0)
    telegram_user_ref_hash: Sha256Hash
    scenario: TelegramClientScenario
    replied: bool
    audit_hash: AuditHash


class TelegramTelethonUpdateFailure(SharedBaseModel):
    message_id: int | None = Field(default=None, ge=0)
    error_code: str = Field(min_length=1, max_length=128)
    retryable: bool


class TelegramTelethonPollResult(SharedBaseModel):
    tenant_id: TenantId
    session_ref_hash: str = Field(pattern=_SHA256_HASH_PATTERN)
    source_ref_hash: str = Field(pattern=_SHA256_HASH_PATTERN)
    handled: tuple[TelegramTelethonHandledUpdate, ...] = Field(default_factory=tuple)
    failed: tuple[TelegramTelethonUpdateFailure, ...] = Field(default_factory=tuple)
    next_offset_id: int | None = Field(default=None, ge=0)


@dataclass(slots=True)
class TelegramTelethonInboundBridge:
    """Reads Telethon messages and routes them into TelegramClientGateway."""

    client_provider: TelegramTelethonClientProvider
    gateway: TelegramClientGateway
    rate_limiter: TelegramTelethonRateLimiter = field(
        default_factory=TelegramTelethonRateLimiter
    )
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    async def poll_once(
        self,
        request: TelegramTelethonPollRequest,
    ) -> TelegramTelethonPollResult:
        handled: list[TelegramTelethonHandledUpdate] = []
        failed: list[TelegramTelethonUpdateFailure] = []
        next_offset_id: int | None = request.offset_id

        try:
            async with self.client_provider.connected(
                tenant_id=request.tenant_id,
                session_ref=request.session_ref,
            ) as client:
                async for raw_message in client.iter_messages(
                    request.source,
                    **_iter_messages_options(request),
                ):
                    message_id = _message_id(raw_message)
                    if message_id is not None:
                        next_offset_id = max(next_offset_id or 0, message_id)

                    inbound = _inbound_message_from_telethon(
                        raw_message,
                        tenant_id=request.tenant_id,
                        correlation_id=request.correlation_id,
                    )
                    if inbound is None:
                        continue

                    try:
                        exchange = await self.gateway.handle_update(inbound)
                        await self._reply(
                            client=client,
                            request=request,
                            inbound=inbound,
                            message_id=message_id,
                            reply_text=exchange.reply.text,
                        )
                    except Exception as error:
                        failed.append(
                            _update_failure_from_error(
                                error,
                                message_id=message_id,
                            )
                        )
                        continue

                    handled.append(
                        TelegramTelethonHandledUpdate(
                            message_id=message_id or 0,
                            telegram_user_ref_hash=exchange.telegram_user_ref_hash,
                            scenario=exchange.scenario,
                            replied=request.reply_to_messages,
                            audit_hash=exchange.audit_hash,
                        )
                    )
        except Exception as error:
            failed.append(_update_failure_from_error(error, message_id=None))

        return TelegramTelethonPollResult(
            tenant_id=request.tenant_id,
            session_ref_hash=_scoped_hash(
                tenant_id=request.tenant_id,
                value=request.session_ref,
            ),
            source_ref_hash=_scoped_hash(
                tenant_id=request.tenant_id,
                value=request.source,
            ),
            handled=tuple(handled),
            failed=tuple(failed),
            next_offset_id=next_offset_id,
        )

    async def _reply(
        self,
        *,
        client: TelegramTelethonClient,
        request: TelegramTelethonPollRequest,
        inbound: TelegramInboundMessage,
        message_id: int | None,
        reply_text: str,
    ) -> None:
        if not request.reply_to_messages:
            return

        target = inbound.chat_id or inbound.telegram_user_id
        await self.rate_limiter.acquire(
            tenant_id=request.tenant_id,
            session_ref=request.session_ref,
            target_ref=target,
            action="reply",
        )
        kwargs: dict[str, object] = {}
        if message_id is not None:
            kwargs["reply_to"] = message_id
        await client.send_message(target, reply_text, **kwargs)


async def _default_sleep(delay_seconds: float) -> None:
    await asyncio.sleep(delay_seconds)


def _session_ref_from_command(command: PlatformPublishCommand) -> str:
    metadata = command.metadata.get(TELEGRAM_PLATFORM)
    if isinstance(metadata, dict):
        session_ref = metadata.get("session_ref")
        if isinstance(session_ref, str) and session_ref.strip() != "":
            return session_ref.strip()

    return command.access_token.get_secret_value()


def _telethon_send_options(metadata: Mapping[str, JSONValue]) -> dict[str, object]:
    options: dict[str, object] = {}
    telegram_metadata = metadata.get(TELEGRAM_PLATFORM)
    if not isinstance(telegram_metadata, dict):
        return options

    for field_name in (
        "parse_mode",
        "link_preview",
        "reply_to",
        "silent",
        "schedule",
        "comment_to",
    ):
        if field_name in telegram_metadata:
            options[field_name] = telegram_metadata[field_name]

    return options


def _publication_error_from_telethon(
    error: Exception,
    *,
    platform: str,
) -> PlatformPublicationError:
    if isinstance(error, PlatformPublicationError):
        return error

    if isinstance(error, TelegramTelethonAuthorizationError):
        return PlatformPublicationError(
            "Telethon-сессия не авторизована",
            platform=platform,
            error_code="auth_failed",
            retryable=False,
        )

    if isinstance(error, TelegramTelethonSessionNotFoundError):
        return PlatformPublicationError(
            "Telethon-сессия не найдена",
            platform=platform,
            error_code="session_not_found",
            retryable=False,
        )

    retry_after = _retry_after_seconds(error)
    error_name = error.__class__.__name__.lower()
    if retry_after is not None or "flood" in error_name:
        return PlatformPublicationError(
            "Telethon вернул лимит Telegram",
            platform=platform,
            error_code="rate_limited",
            retryable=True,
            retry_after_seconds=retry_after,
        )

    if any(marker in error_name for marker in ("unauthorized", "authkey", "auth")):
        return PlatformPublicationError(
            "Telethon отклонил авторизацию",
            platform=platform,
            error_code="auth_failed",
            retryable=False,
        )

    if any(marker in error_name for marker in ("forbidden", "private", "write")):
        return PlatformPublicationError(
            "Telethon запретил публикацию в цель",
            platform=platform,
            error_code="access_denied",
            retryable=False,
        )

    if any(marker in error_name for marker in ("invalid", "empty", "toolong")):
        return PlatformPublicationError(
            "Telethon отклонил параметры запроса",
            platform=platform,
            error_code="invalid_request",
            retryable=False,
        )

    if any(marker in error_name for marker in ("timeout", "server", "rpc")):
        return PlatformPublicationError(
            "Telethon временно недоступен",
            platform=platform,
            error_code="platform_unavailable",
            retryable=True,
        )

    return PlatformPublicationError(
        "Сбой Telethon-интеграции Telegram",
        platform=platform,
        error_code="publication_failed",
        retryable=True,
    )


def _update_failure_from_error(
    error: Exception,
    *,
    message_id: int | None,
) -> TelegramTelethonUpdateFailure:
    if isinstance(error, TelegramAccountNotLinkedError):
        return TelegramTelethonUpdateFailure(
            message_id=message_id,
            error_code="account_not_linked",
            retryable=False,
        )
    if isinstance(error, TelegramClientError):
        return TelegramTelethonUpdateFailure(
            message_id=message_id,
            error_code="client_gateway_failed",
            retryable=False,
        )

    publication_error = _publication_error_from_telethon(
        error,
        platform=TELEGRAM_PLATFORM,
    )
    return TelegramTelethonUpdateFailure(
        message_id=message_id,
        error_code=publication_error.error_code,
        retryable=publication_error.retryable,
    )


def _retry_after_seconds(error: Exception) -> float | None:
    for attribute in ("seconds", "retry_after", "value"):
        value = _attr(error, attribute)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float) and value >= 0:
            return float(value)

    return None


def _iter_messages_options(
    request: TelegramTelethonPollRequest,
) -> dict[str, object]:
    options: dict[str, object] = {"limit": request.limit}
    if request.offset_id is not None:
        options["offset_id"] = request.offset_id

    return options


def _inbound_message_from_telethon(
    raw_message: object,
    *,
    tenant_id: str,
    correlation_id: str,
) -> TelegramInboundMessage | None:
    telegram_user_id = _telegram_sender_id(raw_message)
    text = _message_text(raw_message)
    if telegram_user_id is None or text is None:
        return None

    chat_id = _telegram_chat_id(raw_message)
    return TelegramInboundMessage(
        tenant_id=tenant_id,
        telegram_user_id=telegram_user_id,
        chat_id=chat_id,
        text=text,
        correlation_id=correlation_id,
        received_at=_message_datetime(raw_message, fallback=datetime.now(UTC)),
    )


def _telegram_sender_id(raw_message: object) -> str | None:
    sender_id = _attr(raw_message, "sender_id")
    if isinstance(sender_id, bool) or sender_id is None:
        return None
    if isinstance(sender_id, int | str):
        normalized = str(sender_id).strip()
        return normalized or None

    from_id = _attr(raw_message, "from_id")
    user_id = _attr(from_id, "user_id") if from_id is not None else None
    if isinstance(user_id, bool) or user_id is None:
        return None
    if isinstance(user_id, int | str):
        normalized = str(user_id).strip()
        return normalized or None

    return None


def _telegram_chat_id(raw_message: object) -> str | None:
    chat_id = _attr(raw_message, "chat_id")
    if isinstance(chat_id, bool) or chat_id is None:
        return None
    if isinstance(chat_id, int | str):
        normalized = str(chat_id).strip()
        return normalized or None

    return None


def _message_text(raw_message: object) -> str | None:
    for attribute in ("raw_text", "message", "text"):
        value = _attr(raw_message, attribute)
        if isinstance(value, str) and value.strip() != "":
            return value.strip()

    return None


def _message_id(raw_message: object) -> int | None:
    message_id = _attr(raw_message, "id")
    if isinstance(message_id, bool) or message_id is None:
        return None
    if isinstance(message_id, int):
        return message_id
    if isinstance(message_id, str) and message_id.isdigit():
        return int(message_id)

    return None


def _chat_ref(message: object, fallback: str) -> str:
    chat_id = _attr(message, "chat_id")
    if isinstance(chat_id, bool) or chat_id is None:
        return fallback
    if isinstance(chat_id, int | str):
        normalized = str(chat_id).strip()
        return normalized or fallback

    return fallback


def _message_datetime(message: object, *, fallback: datetime) -> datetime:
    value = _attr(message, "date")
    if isinstance(value, datetime):
        return _normalize_datetime(value)

    return _normalize_datetime(fallback)


def _attr(source: object, name: str) -> object | None:
    return getattr(source, name, None)


async def _sleep(sleeper: TelethonDelaySleeper, delay_seconds: float) -> None:
    sleep_result = sleeper(delay_seconds)
    if inspect.isawaitable(sleep_result):
        await sleep_result


def _normalize_datetime(value: datetime | str) -> datetime:
    if isinstance(value, str):
        normalized = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        normalized = value

    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=UTC)

    return normalized.astimezone(UTC)


def _scoped_hash(*, tenant_id: str, value: str) -> str:
    return "sha256:" + hashlib.sha256(f"{tenant_id}:{value}".encode()).hexdigest()


def new_telegram_telethon_session_ref(prefix: str = "tg-session") -> IdempotencyKey:
    return f"{prefix}-{uuid4()}"


__all__ = [
    "TELEGRAM_TELETHON_CONNECTOR_NAME",
    "TELEGRAM_TELETHON_SESSION_AAD_LABEL",
    "InMemoryTelegramTelethonSessionStore",
    "TelegramTelethonAuthorizationError",
    "TelegramTelethonClient",
    "TelegramTelethonClientConfig",
    "TelegramTelethonClientFactory",
    "TelegramTelethonClientProvider",
    "TelegramTelethonError",
    "TelegramTelethonHandledUpdate",
    "TelegramTelethonInboundBridge",
    "TelegramTelethonPollRequest",
    "TelegramTelethonPollResult",
    "TelegramTelethonPublisher",
    "TelegramTelethonRateLimit",
    "TelegramTelethonRateLimiter",
    "TelegramTelethonSessionClientProvider",
    "TelegramTelethonSessionNotFoundError",
    "TelegramTelethonSessionRecord",
    "TelegramTelethonSessionRepository",
    "TelegramTelethonUpdateFailure",
    "TelethonClientFactory",
    "new_telegram_telethon_session_ref",
    "telethon_string_session_from_client",
]
