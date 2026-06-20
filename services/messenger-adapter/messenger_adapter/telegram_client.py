"""Telegram-клиент для участников НМЦ (issue #71).

Модуль реализует клиентский интерфейс взаимодействия через Telegram поверх
Unified Messenger Adapter: базовые сценарии участника (/start, /help, /status,
/balance, /tasks), шифрование чувствительных данных (идентичность Telegram
через AES-256-GCM) и устойчивую работу через ротацию прокси (HTTP/SOCKS5/
MTProto). В отличие от ``TelegramBotApiPublisher`` (исходящая публикация),
здесь обрабатывается входящий диалог с кооперативным участником.

Принципы безопасности (docs/SECURITY.md §4.1):
- сырой Telegram-идентификатор не хранится и не попадает в события/аудит —
  только шифртекст и детерминированный ``telegram_user_ref_hash``;
- доступ к данным участника строго привязан к ``tenant_id`` (AAD шифра и
  ключи хранилищ включают tenant);
- параметры прокси (url, secret_ref) не покидают границу сервиса — наружу
  отдаются только ``redacted_url`` и SHA-256 хэши.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Protocol
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from libs.shared.audit_logger import AuditLogger
from libs.shared.events import EventEnvelope, EventPublisher, InMemoryEventBus
from libs.shared.models import (
    AuditHash,
    CorrelationId,
    IdempotencyKey,
    JSONValue,
    SharedBaseModel,
    SubjectId,
    TenantId,
)
from libs.shared.tenant import TenantIsolationError
from messenger_adapter.base_adapter import PlatformTokenCipher

TELEGRAM_CLIENT_SOURCE = "messenger-adapter"
TELEGRAM_CLIENT_SCHEMA_VERSION = "1.0"
TELEGRAM_IDENTITY_AAD_LABEL = "telegram_client_identity"
TELEGRAM_ACCOUNT_LINKED_EVENT = "messenger.telegram_client.account_linked"
TELEGRAM_COMMAND_HANDLED_EVENT = "messenger.telegram_client.command_handled"

_PROXY_URL_MAX_LENGTH = 2_048
_SHA256_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"
_TOKEN_CIPHERTEXT_PATTERN = r"^aes256gcm:[A-Za-z0-9_-]+={0,2}$"
_SECRET_REF_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"
_TELEGRAM_USER_ID_PATTERN = r"^[0-9]{1,20}$"
_TELEGRAM_CHAT_ID_PATTERN = r"^-?[0-9]{1,20}$"
_HTTP_SCHEMES = frozenset({"http", "https"})
_SOCKS5_SCHEMES = frozenset({"socks5", "socks5h"})
_MTPROTO_SCHEMES = frozenset({"mtproto"})

Sha256Hash = Annotated[
    str,
    Field(min_length=71, max_length=71, pattern=_SHA256_HASH_PATTERN),
]


class TelegramClientError(RuntimeError):
    """Base error for the participant-facing Telegram client."""


class TelegramAccountNotLinkedError(TelegramClientError):
    """Raised when no linked participant is found for a Telegram identity."""


class TelegramProxyConfigurationError(TelegramClientError):
    """Raised when a Telegram proxy pool is configured incorrectly."""


class TelegramProxyUnavailableError(TelegramClientError):
    """Raised when a Telegram proxy pool has no live proxy to lease."""


class TelegramClientScenario(StrEnum):
    START = "start"
    HELP = "help"
    STATUS = "status"
    BALANCE = "balance"
    TASKS = "tasks"
    UNKNOWN = "unknown"


class TelegramProxyProtocol(StrEnum):
    HTTP = "http"
    SOCKS5 = "socks5"
    MTPROTO = "mtproto"


class TelegramProxyHealth(StrEnum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DISABLED = "disabled"


class TelegramProxyRotationStrategy(StrEnum):
    ROUND_ROBIN = "round_robin"


class TelegramIdentityCipher:
    """AES-256-GCM cipher for Telegram identities with domain-separated AAD.

    Переиспользует проверенный ``PlatformTokenCipher`` Unified Messenger
    Adapter, но привязывает AAD к выделенной метке ``telegram_client_identity``
    (плюс tenant_id), чтобы шифртексты идентичностей нельзя было подменить
    токенами публикации того же tenant.
    """

    def __init__(self, encryption_key: str | bytes | PlatformTokenCipher) -> None:
        self._cipher = (
            encryption_key
            if isinstance(encryption_key, PlatformTokenCipher)
            else PlatformTokenCipher(encryption_key)
        )

    def encrypt(self, *, tenant_id: str, telegram_user_id: str) -> str:
        return self._cipher.encrypt(
            tenant_id=tenant_id,
            platform=TELEGRAM_IDENTITY_AAD_LABEL,
            token=telegram_user_id,
        )

    def decrypt(self, *, tenant_id: str, identity_encrypted: str) -> str:
        return self._cipher.decrypt(
            tenant_id=tenant_id,
            platform=TELEGRAM_IDENTITY_AAD_LABEL,
            token_encrypted=identity_encrypted,
        )


class TelegramAccountLink(SharedBaseModel):
    """Tenant-scoped binding between a participant and a Telegram identity."""

    tenant_id: TenantId
    member_id: SubjectId
    link_id: IdempotencyKey
    telegram_user_ref_hash: Sha256Hash
    identity_encrypted: str = Field(pattern=_TOKEN_CIPHERTEXT_PATTERN)
    linked_at: datetime
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("linked_at")
    @classmethod
    def _normalize_linked_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


@dataclass(slots=True)
class InMemoryTelegramAccountStore:
    """In-memory account-link store for unit tests and local wiring."""

    _by_ref_hash: dict[tuple[str, str], TelegramAccountLink] = field(
        default_factory=dict,
        init=False,
    )
    _by_member: dict[tuple[str, str], TelegramAccountLink] = field(
        default_factory=dict,
        init=False,
    )

    def save(self, link: TelegramAccountLink) -> TelegramAccountLink:
        self._by_ref_hash[(link.tenant_id, link.telegram_user_ref_hash)] = link
        self._by_member[(link.tenant_id, link.member_id)] = link
        return link

    def find_by_ref_hash(
        self,
        *,
        tenant_id: str,
        telegram_user_ref_hash: str,
    ) -> TelegramAccountLink | None:
        return self._by_ref_hash.get((tenant_id, telegram_user_ref_hash))

    def require_by_ref_hash(
        self,
        *,
        tenant_id: str,
        telegram_user_ref_hash: str,
    ) -> TelegramAccountLink:
        link = self.find_by_ref_hash(
            tenant_id=tenant_id,
            telegram_user_ref_hash=telegram_user_ref_hash,
        )
        if link is None:
            raise TelegramAccountNotLinkedError(
                "Telegram-аккаунт не связан с участником tenant"
            )

        return link

    def find_by_member(
        self,
        *,
        tenant_id: str,
        member_id: str,
    ) -> TelegramAccountLink | None:
        return self._by_member.get((tenant_id, member_id))


class TelegramInboundMessage(SharedBaseModel):
    """Sanitized inbound Telegram update routed to the client gateway."""

    tenant_id: TenantId
    telegram_user_id: str = Field(
        min_length=1,
        max_length=20,
        pattern=_TELEGRAM_USER_ID_PATTERN,
    )
    text: str = Field(min_length=1, max_length=4096)
    correlation_id: CorrelationId
    chat_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=21,
        pattern=_TELEGRAM_CHAT_ID_PATTERN,
    )
    received_at: datetime | None = None

    @field_validator("received_at")
    @classmethod
    def _normalize_received_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None

        return _normalize_datetime(value)


class TelegramClientCommand(SharedBaseModel):
    """Parsed participant command derived from inbound message text."""

    scenario: TelegramClientScenario
    raw_text: str = Field(min_length=1, max_length=4096)
    argument: str | None = Field(default=None, max_length=4096)


class TelegramMemberSnapshot(SharedBaseModel):
    """Minimal participant projection rendered by scenario handlers."""

    tenant_id: TenantId
    member_id: SubjectId
    status_label: str = Field(min_length=1, max_length=128)
    contribution_weight: float = Field(ge=0)
    points_balance: int = Field(ge=0)
    open_task_titles: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("open_task_titles")
    @classmethod
    def _validate_titles(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(title.strip() == "" for title in value):
            raise ValueError("open_task_titles не должен содержать пустые строки")

        return value


class TelegramMemberContextProvider(Protocol):
    def snapshot(self, *, tenant_id: str, member_id: str) -> TelegramMemberSnapshot:
        """Return a tenant-scoped participant snapshot for rendering."""


@dataclass(slots=True)
class InMemoryTelegramMemberContextProvider:
    """In-memory participant snapshot provider for tests and local wiring."""

    _snapshots: dict[tuple[str, str], TelegramMemberSnapshot] = field(
        default_factory=dict,
        init=False,
    )

    def save(self, snapshot: TelegramMemberSnapshot) -> TelegramMemberSnapshot:
        self._snapshots[(snapshot.tenant_id, snapshot.member_id)] = snapshot
        return snapshot

    def snapshot(self, *, tenant_id: str, member_id: str) -> TelegramMemberSnapshot:
        snapshot = self._snapshots.get((tenant_id, member_id))
        if snapshot is None:
            raise TelegramClientError("Снимок участника не найден для tenant/member")

        return snapshot


class TelegramClientReply(SharedBaseModel):
    """Reply text produced for a participant scenario."""

    scenario: TelegramClientScenario
    text: str = Field(min_length=1, max_length=4096)
    contains_member_data: bool = False


@dataclass(frozen=True, slots=True)
class TelegramScenarioContext:
    """Immutable context passed to scenario handlers."""

    tenant_id: str
    member_id: str
    command: TelegramClientCommand
    correlation_id: str
    snapshot_provider: TelegramMemberContextProvider


TelegramScenarioHandler = Callable[[TelegramScenarioContext], TelegramClientReply]
TelegramScenarioHandlerMap = dict[TelegramClientScenario, TelegramScenarioHandler]


def _handle_start(context: TelegramScenarioContext) -> TelegramClientReply:
    return TelegramClientReply(
        scenario=TelegramClientScenario.START,
        text=(
            "Telegram-аккаунт подтверждён и связан с вашим профилем участника. "
            "Доступные команды: /help, /status, /balance, /tasks."
        ),
        contains_member_data=False,
    )


def _handle_help(context: TelegramScenarioContext) -> TelegramClientReply:
    return TelegramClientReply(
        scenario=TelegramClientScenario.HELP,
        text=(
            "Доступные команды:\n"
            "/status — текущий статус участия;\n"
            "/balance — баллы и коэффициент вклада (Кв);\n"
            "/tasks — открытые задачи;\n"
            "/help — эта подсказка."
        ),
        contains_member_data=False,
    )


def _handle_status(context: TelegramScenarioContext) -> TelegramClientReply:
    snapshot = context.snapshot_provider.snapshot(
        tenant_id=context.tenant_id,
        member_id=context.member_id,
    )
    return TelegramClientReply(
        scenario=TelegramClientScenario.STATUS,
        text=f"Статус участия: {snapshot.status_label}.",
        contains_member_data=True,
    )


def _handle_balance(context: TelegramScenarioContext) -> TelegramClientReply:
    snapshot = context.snapshot_provider.snapshot(
        tenant_id=context.tenant_id,
        member_id=context.member_id,
    )
    return TelegramClientReply(
        scenario=TelegramClientScenario.BALANCE,
        text=(
            f"Баллы: {snapshot.points_balance}. "
            f"Коэффициент вклада (Кв): {snapshot.contribution_weight:g}."
        ),
        contains_member_data=True,
    )


def _handle_tasks(context: TelegramScenarioContext) -> TelegramClientReply:
    snapshot = context.snapshot_provider.snapshot(
        tenant_id=context.tenant_id,
        member_id=context.member_id,
    )
    if snapshot.open_task_titles:
        listed = "\n".join(f"• {title}" for title in snapshot.open_task_titles)
        text = f"Открытые задачи:\n{listed}"
    else:
        text = "Открытых задач нет."

    return TelegramClientReply(
        scenario=TelegramClientScenario.TASKS,
        text=text,
        contains_member_data=True,
    )


def _handle_unknown(context: TelegramScenarioContext) -> TelegramClientReply:
    return TelegramClientReply(
        scenario=TelegramClientScenario.UNKNOWN,
        text=(
            "Команда не распознана. Отправьте /help, чтобы увидеть доступные действия."
        ),
        contains_member_data=False,
    )


def default_scenario_handlers() -> TelegramScenarioHandlerMap:
    return {
        TelegramClientScenario.START: _handle_start,
        TelegramClientScenario.HELP: _handle_help,
        TelegramClientScenario.STATUS: _handle_status,
        TelegramClientScenario.BALANCE: _handle_balance,
        TelegramClientScenario.TASKS: _handle_tasks,
        TelegramClientScenario.UNKNOWN: _handle_unknown,
    }


@dataclass(slots=True)
class TelegramScenarioRouter:
    """Maps parsed scenarios to handlers with an UNKNOWN fallback."""

    handlers: dict[TelegramClientScenario, TelegramScenarioHandler]

    @classmethod
    def with_defaults(cls) -> TelegramScenarioRouter:
        return cls(handlers=default_scenario_handlers())

    def register(
        self,
        scenario: TelegramClientScenario,
        handler: TelegramScenarioHandler,
    ) -> None:
        self.handlers[scenario] = handler

    def dispatch(self, context: TelegramScenarioContext) -> TelegramClientReply:
        handler = self.handlers.get(context.command.scenario)
        if handler is None:
            handler = self.handlers[TelegramClientScenario.UNKNOWN]

        return handler(context)


_SCENARIO_BY_KEYWORD: dict[str, TelegramClientScenario] = {
    "start": TelegramClientScenario.START,
    "help": TelegramClientScenario.HELP,
    "menu": TelegramClientScenario.HELP,
    "status": TelegramClientScenario.STATUS,
    "balance": TelegramClientScenario.BALANCE,
    "tasks": TelegramClientScenario.TASKS,
    "task": TelegramClientScenario.TASKS,
}


def parse_telegram_command(text: str) -> TelegramClientCommand:
    """Parse participant text into a scenario command.

    Поддерживает формы ``/balance``, ``balance`` и ``/start@bot_name``,
    извлекая аргумент после первого пробела.
    """

    stripped = text.strip()
    if stripped == "":
        raise TelegramClientError("Пустой текст команды не поддерживается")

    head, _, rest = stripped.partition(" ")
    keyword = head.lstrip("/").split("@", 1)[0].lower()
    scenario = _SCENARIO_BY_KEYWORD.get(keyword, TelegramClientScenario.UNKNOWN)
    argument = rest.strip() or None

    return TelegramClientCommand(
        scenario=scenario,
        raw_text=stripped,
        argument=argument,
    )


class TelegramProxyEndpoint(SharedBaseModel):
    """Proxy endpoint configuration with credentials kept out of the URL."""

    proxy_id: IdempotencyKey
    protocol: TelegramProxyProtocol
    url: str = Field(min_length=1, max_length=_PROXY_URL_MAX_LENGTH)
    secret_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=_SECRET_REF_PATTERN,
    )
    priority: int = Field(default=100, ge=0, le=10_000)
    enabled: bool = True
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme == "" or parts.netloc == "":
            raise ValueError("url должен быть абсолютным proxy URL")
        if parts.username is not None or parts.password is not None:
            raise ValueError(
                "proxy credentials нельзя хранить в url; используйте secret_ref"
            )

        return urlunsplit(
            (
                parts.scheme.lower(),
                parts.netloc,
                parts.path,
                parts.query,
                parts.fragment,
            )
        )

    @model_validator(mode="after")
    def _validate_protocol_scheme(self) -> TelegramProxyEndpoint:
        scheme = urlsplit(self.url).scheme.lower()
        if self.protocol is TelegramProxyProtocol.HTTP and scheme not in _HTTP_SCHEMES:
            raise ValueError("HTTP proxy должен использовать схему http или https")
        if (
            self.protocol is TelegramProxyProtocol.SOCKS5
            and scheme not in _SOCKS5_SCHEMES
        ):
            raise ValueError(
                "SOCKS5 proxy должен использовать схему socks5 или socks5h"
            )
        if (
            self.protocol is TelegramProxyProtocol.MTPROTO
            and scheme not in _MTPROTO_SCHEMES
        ):
            raise ValueError("MTProto proxy должен использовать схему mtproto")

        return self


class TelegramProxyState(SharedBaseModel):
    """Redacted, public-safe view of a proxy endpoint for observability."""

    proxy_id: IdempotencyKey
    protocol: TelegramProxyProtocol
    redacted_url: str = Field(min_length=1, max_length=_PROXY_URL_MAX_LENGTH)
    url_hash: Sha256Hash
    secret_ref_hash: Sha256Hash | None = None
    priority: int = Field(ge=0, le=10_000)
    health_status: TelegramProxyHealth


class TelegramProxyLease(SharedBaseModel):
    """Redacted lease describing the proxy selected for one interaction."""

    tenant_id: TenantId
    pool_id: IdempotencyKey
    lease_id: IdempotencyKey
    proxy_id: IdempotencyKey
    protocol: TelegramProxyProtocol
    redacted_url: str = Field(min_length=1, max_length=_PROXY_URL_MAX_LENGTH)
    url_hash: Sha256Hash
    secret_ref_hash: Sha256Hash | None = None
    health_status: TelegramProxyHealth
    rotation_strategy: TelegramProxyRotationStrategy
    selected_at: datetime

    @field_validator("selected_at")
    @classmethod
    def _normalize_selected_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class TelegramProxyRotator:
    """Tenant-scoped round-robin rotator over healthy Telegram proxies."""

    def __init__(
        self,
        *,
        tenant_id: str,
        pool_id: str,
        endpoints: Sequence[TelegramProxyEndpoint],
        rotation_strategy: TelegramProxyRotationStrategy = (
            TelegramProxyRotationStrategy.ROUND_ROBIN
        ),
    ) -> None:
        if len(endpoints) == 0:
            raise TelegramProxyConfigurationError(
                "Telegram прокси-пул должен содержать хотя бы один proxy"
            )

        ordered = tuple(
            sorted(endpoints, key=lambda item: (item.priority, item.proxy_id))
        )
        _ensure_unique_proxy_ids(ordered)
        self.tenant_id = tenant_id
        self.pool_id = pool_id
        self.rotation_strategy = rotation_strategy
        self._endpoints: tuple[TelegramProxyEndpoint, ...] = ordered
        self._health: dict[str, TelegramProxyHealth] = {
            endpoint.proxy_id: (
                TelegramProxyHealth.HEALTHY
                if endpoint.enabled
                else TelegramProxyHealth.DISABLED
            )
            for endpoint in ordered
        }
        self._cursor = 0

    @property
    def total_count(self) -> int:
        return len(self._endpoints)

    @property
    def healthy_count(self) -> int:
        return sum(
            1
            for status in self._health.values()
            if status is TelegramProxyHealth.HEALTHY
        )

    @property
    def protocols(self) -> tuple[str, ...]:
        return tuple(sorted({endpoint.protocol.value for endpoint in self._endpoints}))

    def snapshot_states(self) -> tuple[TelegramProxyState, ...]:
        return tuple(
            TelegramProxyState(
                proxy_id=endpoint.proxy_id,
                protocol=endpoint.protocol,
                redacted_url=_redacted_proxy_url(endpoint.url),
                url_hash=_scoped_hash(endpoint.url),
                secret_ref_hash=(
                    _scoped_hash(endpoint.secret_ref)
                    if endpoint.secret_ref is not None
                    else None
                ),
                priority=endpoint.priority,
                health_status=self._health[endpoint.proxy_id],
            )
            for endpoint in self._endpoints
        )

    def mark_unhealthy(self, proxy_id: str) -> None:
        self._set_health(proxy_id, TelegramProxyHealth.UNHEALTHY)

    def mark_healthy(self, proxy_id: str) -> None:
        self._set_health(proxy_id, TelegramProxyHealth.HEALTHY)

    def lease(self, *, selected_at: datetime | str | None = None) -> TelegramProxyLease:
        selected_at_dt = _normalize_datetime(selected_at or datetime.now(UTC))
        index, endpoint = self._select_live()
        self._cursor = (index + 1) % len(self._endpoints)

        return TelegramProxyLease(
            tenant_id=self.tenant_id,
            pool_id=self.pool_id,
            lease_id=_new_id("tg-proxy-lease"),
            proxy_id=endpoint.proxy_id,
            protocol=endpoint.protocol,
            redacted_url=_redacted_proxy_url(endpoint.url),
            url_hash=_scoped_hash(endpoint.url),
            secret_ref_hash=(
                _scoped_hash(endpoint.secret_ref)
                if endpoint.secret_ref is not None
                else None
            ),
            health_status=self._health[endpoint.proxy_id],
            rotation_strategy=self.rotation_strategy,
            selected_at=selected_at_dt,
        )

    def _select_live(self) -> tuple[int, TelegramProxyEndpoint]:
        for offset in range(len(self._endpoints)):
            index = (self._cursor + offset) % len(self._endpoints)
            endpoint = self._endpoints[index]
            if self._health.get(endpoint.proxy_id) is TelegramProxyHealth.HEALTHY:
                return index, endpoint

        raise TelegramProxyUnavailableError("В Telegram прокси-пуле нет живых proxy")

    def _set_health(self, proxy_id: str, status: TelegramProxyHealth) -> None:
        if proxy_id not in self._health:
            raise TelegramProxyConfigurationError(
                f"proxy {proxy_id} не найден в пуле {self.pool_id}"
            )
        if self._health[proxy_id] is TelegramProxyHealth.DISABLED:
            return

        self._health[proxy_id] = status


class TelegramProxyDirectory(Protocol):
    def get(self, *, tenant_id: str) -> TelegramProxyRotator | None:
        """Return the tenant-scoped proxy rotator, if any."""


@dataclass(slots=True)
class InMemoryTelegramProxyDirectory:
    """Tenant-scoped registry of proxy rotators (one pool per tenant)."""

    _rotators: dict[str, TelegramProxyRotator] = field(
        default_factory=dict,
        init=False,
    )

    def register(self, rotator: TelegramProxyRotator) -> TelegramProxyRotator:
        self._rotators[rotator.tenant_id] = rotator
        return rotator

    def get(self, *, tenant_id: str) -> TelegramProxyRotator | None:
        return self._rotators.get(tenant_id)


class TelegramClientExchange(SharedBaseModel):
    """Result of handling one inbound participant interaction."""

    tenant_id: TenantId
    member_id: SubjectId
    telegram_user_ref_hash: Sha256Hash
    scenario: TelegramClientScenario
    reply: TelegramClientReply
    proxy_lease: TelegramProxyLease | None = None
    audit_hash: AuditHash
    correlation_id: CorrelationId
    handled_at: datetime

    @field_validator("handled_at")
    @classmethod
    def _normalize_handled_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


@dataclass(slots=True)
class TelegramClientGateway:
    """Orchestrates participant interactions over Telegram.

    Связывает шифрование идентичности (issue #71 критерий «защищённая
    передача»), диспетчеризацию базовых сценариев (критерий «сценарии через
    Telegram») и аренду прокси из tenant-пула (критерий «работа через
    прокси»), фиксируя аудит и доменные события без раскрытия секретов.
    """

    identity_cipher: TelegramIdentityCipher
    account_store: InMemoryTelegramAccountStore = field(
        default_factory=InMemoryTelegramAccountStore
    )
    member_provider: TelegramMemberContextProvider = field(
        default_factory=InMemoryTelegramMemberContextProvider
    )
    scenario_router: TelegramScenarioRouter = field(
        default_factory=TelegramScenarioRouter.with_defaults
    )
    proxy_directory: TelegramProxyDirectory | None = None
    event_publisher: EventPublisher = field(default_factory=InMemoryEventBus)
    audit_logger: AuditLogger = field(default_factory=AuditLogger)
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    async def link_account(
        self,
        *,
        tenant_id: str,
        member_id: str,
        telegram_user_id: str,
        correlation_id: str,
        link_id: str | None = None,
        linked_at: datetime | str | None = None,
        event_id: str | None = None,
        metadata: Mapping[str, JSONValue] | None = None,
    ) -> TelegramAccountLink:
        linked_at_dt = _normalize_datetime(linked_at or datetime.now(UTC))
        ref_hash = telegram_user_ref_hash(
            tenant_id=tenant_id,
            telegram_user_id=telegram_user_id,
        )
        identity_encrypted = self.identity_cipher.encrypt(
            tenant_id=tenant_id,
            telegram_user_id=telegram_user_id,
        )
        link = self.account_store.save(
            TelegramAccountLink(
                tenant_id=tenant_id,
                member_id=member_id,
                link_id=link_id or _new_id("tg-link"),
                telegram_user_ref_hash=ref_hash,
                identity_encrypted=identity_encrypted,
                linked_at=linked_at_dt,
                metadata=dict(metadata or {}),
            )
        )
        audit_metadata: dict[str, JSONValue] = {
            "link_id": link.link_id,
            "telegram_user_ref_hash": ref_hash,
            "metadata_keys": _json_string_list(sorted(link.metadata)),
        }
        audit_record = self.audit_logger.record(
            event_type=TELEGRAM_ACCOUNT_LINKED_EVENT,
            tenant_id=tenant_id,
            metadata=audit_metadata,
            timestamp=linked_at_dt,
            correlation_id=correlation_id,
            actor_hash=subject_ref_hash(tenant_id=tenant_id, subject_id=member_id),
            source=TELEGRAM_CLIENT_SOURCE,
        )
        self.logger.debug(
            "Telegram-аккаунт связан с участником",
            extra={
                "tenant_id": tenant_id,
                "link_id": link.link_id,
                "telegram_user_ref_hash": ref_hash,
                "correlation_id": correlation_id,
            },
        )
        await self.event_publisher.publish(
            EventEnvelope(
                event_id=event_id or _new_id("evt-tg-account-linked"),
                type=TELEGRAM_ACCOUNT_LINKED_EVENT,
                schema_version=TELEGRAM_CLIENT_SCHEMA_VERSION,
                tenant_id=tenant_id,
                source=TELEGRAM_CLIENT_SOURCE,
                correlation_id=correlation_id,
                occurred_at=linked_at_dt,
                payload={
                    "link_id": link.link_id,
                    "telegram_user_ref_hash": ref_hash,
                    "audit_hash": audit_record.audit_hash,
                },
            )
        )
        return link

    async def handle_update(
        self,
        message: TelegramInboundMessage,
        *,
        now: datetime | str | None = None,
        event_id: str | None = None,
    ) -> TelegramClientExchange:
        handled_at = _normalize_datetime(
            now or message.received_at or datetime.now(UTC)
        )
        ref_hash = telegram_user_ref_hash(
            tenant_id=message.tenant_id,
            telegram_user_id=message.telegram_user_id,
        )
        link = self.account_store.require_by_ref_hash(
            tenant_id=message.tenant_id,
            telegram_user_ref_hash=ref_hash,
        )
        self._verify_identity_binding(link=link, message=message)

        command = parse_telegram_command(message.text)
        reply = self.scenario_router.dispatch(
            TelegramScenarioContext(
                tenant_id=message.tenant_id,
                member_id=link.member_id,
                command=command,
                correlation_id=message.correlation_id,
                snapshot_provider=self.member_provider,
            )
        )
        proxy_lease = self._lease_proxy(
            tenant_id=message.tenant_id,
            selected_at=handled_at,
        )
        audit_record = self.audit_logger.record(
            event_type=TELEGRAM_COMMAND_HANDLED_EVENT,
            tenant_id=message.tenant_id,
            metadata=_command_audit_metadata(
                ref_hash=ref_hash,
                command=command,
                reply=reply,
                proxy_lease=proxy_lease,
            ),
            timestamp=handled_at,
            correlation_id=message.correlation_id,
            actor_hash=subject_ref_hash(
                tenant_id=message.tenant_id,
                subject_id=link.member_id,
            ),
            source=TELEGRAM_CLIENT_SOURCE,
        )
        self.logger.debug(
            "Обработана команда участника через Telegram",
            extra={
                "tenant_id": message.tenant_id,
                "telegram_user_ref_hash": ref_hash,
                "scenario": command.scenario.value,
                "proxy_id": proxy_lease.proxy_id if proxy_lease else None,
                "correlation_id": message.correlation_id,
            },
        )
        await self.event_publisher.publish(
            EventEnvelope(
                event_id=event_id or _new_id("evt-tg-command-handled"),
                type=TELEGRAM_COMMAND_HANDLED_EVENT,
                schema_version=TELEGRAM_CLIENT_SCHEMA_VERSION,
                tenant_id=message.tenant_id,
                source=TELEGRAM_CLIENT_SOURCE,
                correlation_id=message.correlation_id,
                occurred_at=handled_at,
                payload=_command_event_payload(
                    ref_hash=ref_hash,
                    command=command,
                    reply=reply,
                    proxy_lease=proxy_lease,
                    audit_hash=audit_record.audit_hash,
                ),
            )
        )
        return TelegramClientExchange(
            tenant_id=message.tenant_id,
            member_id=link.member_id,
            telegram_user_ref_hash=ref_hash,
            scenario=reply.scenario,
            reply=reply,
            proxy_lease=proxy_lease,
            audit_hash=audit_record.audit_hash,
            correlation_id=message.correlation_id,
            handled_at=handled_at,
        )

    def _verify_identity_binding(
        self,
        *,
        link: TelegramAccountLink,
        message: TelegramInboundMessage,
    ) -> None:
        decrypted = self.identity_cipher.decrypt(
            tenant_id=message.tenant_id,
            identity_encrypted=link.identity_encrypted,
        )
        if decrypted != message.telegram_user_id:
            raise TelegramClientError(
                "Идентификатор Telegram не совпадает с привязкой участника"
            )

    def _lease_proxy(
        self,
        *,
        tenant_id: str,
        selected_at: datetime,
    ) -> TelegramProxyLease | None:
        if self.proxy_directory is None:
            return None

        rotator = self.proxy_directory.get(tenant_id=tenant_id)
        if rotator is None:
            return None
        if rotator.tenant_id != tenant_id:
            raise TenantIsolationError(
                "Прокси-пул принадлежит другому tenant",
                details={"resource_type": "telegram_proxy_pool"},
            )

        return rotator.lease(selected_at=selected_at)


def telegram_user_ref_hash(*, tenant_id: str, telegram_user_id: str) -> str:
    payload = f"{tenant_id}:telegram_user:{telegram_user_id}".encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def subject_ref_hash(*, tenant_id: str, subject_id: str) -> str:
    payload = f"{tenant_id}:{subject_id}".encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _command_audit_metadata(
    *,
    ref_hash: str,
    command: TelegramClientCommand,
    reply: TelegramClientReply,
    proxy_lease: TelegramProxyLease | None,
) -> dict[str, JSONValue]:
    metadata: dict[str, JSONValue] = {
        "telegram_user_ref_hash": ref_hash,
        "scenario": command.scenario.value,
        "has_argument": command.argument is not None,
        "contains_member_data": reply.contains_member_data,
    }
    if proxy_lease is not None:
        metadata["proxy"] = _proxy_lease_metadata(proxy_lease)

    return metadata


def _command_event_payload(
    *,
    ref_hash: str,
    command: TelegramClientCommand,
    reply: TelegramClientReply,
    proxy_lease: TelegramProxyLease | None,
    audit_hash: str,
) -> dict[str, JSONValue]:
    payload: dict[str, JSONValue] = {
        "telegram_user_ref_hash": ref_hash,
        "scenario": command.scenario.value,
        "contains_member_data": reply.contains_member_data,
        "audit_hash": audit_hash,
    }
    if proxy_lease is not None:
        payload["proxy"] = _proxy_lease_metadata(proxy_lease)

    return payload


def _proxy_lease_metadata(proxy_lease: TelegramProxyLease) -> dict[str, JSONValue]:
    return {
        "pool_id": proxy_lease.pool_id,
        "lease_id": proxy_lease.lease_id,
        "proxy_id": proxy_lease.proxy_id,
        "protocol": proxy_lease.protocol.value,
        "url_hash": proxy_lease.url_hash,
    }


def _ensure_unique_proxy_ids(endpoints: tuple[TelegramProxyEndpoint, ...]) -> None:
    proxy_ids = [endpoint.proxy_id for endpoint in endpoints]
    if len(proxy_ids) != len(set(proxy_ids)):
        raise TelegramProxyConfigurationError(
            "proxy_id должен быть уникальным внутри пула"
        )


def _redacted_proxy_url(value: str) -> str:
    parts = urlsplit(value)
    netloc = parts.netloc
    if parts.username is not None or parts.password is not None:
        hostname = parts.hostname or ""
        port = f":{parts.port}" if parts.port is not None else ""
        netloc = f"{hostname}{port}"

    return urlunsplit((parts.scheme.lower(), netloc, parts.path, parts.query, ""))


def _scoped_hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_string_list(values: Iterable[str]) -> list[JSONValue]:
    return [value for value in values]


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
