from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from pydantic import ConfigDict, Field, field_validator

from libs.shared.models import (
    AuditHash,
    CorrelationId,
    EventType,
    IdempotencyKey,
    JSONValue,
    SharedBaseModel,
    SubjectId,
    TenantId,
)

_EMAIL_ADDRESS_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
_EMAIL_ADDRESS_RE = re.compile(_EMAIL_ADDRESS_PATTERN)
_EMAIL_PROVIDER_PATTERN = r"^[a-z][a-z0-9_-]{1,63}$"
_EMAIL_ROUTE_PATTERN = r"^[a-z][a-z0-9_-]{1,63}$"
_REF_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"
_SECRET_REF_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"
_HTTP_SCHEMES = frozenset({"http", "https"})


class EmailMessagePurpose(StrEnum):
    SYSTEM = "system"
    MARKETING = "marketing"


class EmailOutboxStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    DEFERRED = "deferred"
    FAILED = "failed"


class EmailProviderKind(StrEnum):
    IN_MEMORY = "in_memory"
    POSTALSERVER = "postalserver"
    MAILGUN = "mailgun"
    GENERIC_HTTP = "generic_http"


class EmailProviderStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"


class EmailDeliveryError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str = "email_delivery_failed",
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


class EmailProviderRoute(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    tenant_id: TenantId
    route_key: str = Field(min_length=2, max_length=64, pattern=_EMAIL_ROUTE_PATTERN)
    purpose: EmailMessagePurpose
    provider_name: str = Field(
        min_length=2,
        max_length=64,
        pattern=_EMAIL_PROVIDER_PATTERN,
    )
    provider_kind: EmailProviderKind = EmailProviderKind.GENERIC_HTTP
    sender_alias: str | None = Field(default=None, min_length=1, max_length=320)
    priority: int = Field(default=100, ge=0, le=10_000)
    status: EmailProviderStatus = EmailProviderStatus.ACTIVE
    endpoint_url: str | None = Field(default=None, min_length=1, max_length=2048)
    region: str | None = Field(default=None, min_length=1, max_length=64)
    credentials_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=_SECRET_REF_PATTERN,
    )
    parameters: dict[str, JSONValue] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("route_key", "provider_name", mode="before")
    @classmethod
    def _normalize_key(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("purpose", "provider_kind", "status", mode="before")
    @classmethod
    def _normalize_enum(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("endpoint_url")
    @classmethod
    def _validate_endpoint_url(cls, value: str | None) -> str | None:
        if value is None:
            return None

        parts = urlsplit(value)
        if parts.scheme.lower() not in _HTTP_SCHEMES or parts.netloc == "":
            raise ValueError("endpoint_url должен быть абсолютным HTTP(S) URL")
        if parts.username is not None or parts.password is not None:
            raise ValueError(
                "provider credentials нельзя хранить в endpoint_url; "
                "используйте credentials_ref"
            )

        return urlunsplit(
            (
                parts.scheme.lower(),
                parts.netloc,
                parts.path,
                parts.query,
                "",
            )
        )

    @field_validator("updated_at")
    @classmethod
    def _normalize_updated_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)

    def is_available(self) -> bool:
        return self.status is EmailProviderStatus.ACTIVE


class EmailOutboxMessage(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    message_id: IdempotencyKey
    tenant_id: TenantId
    notification_id: IdempotencyKey
    source_event_id: IdempotencyKey
    source_event_type: EventType
    recipient_id: SubjectId
    to_address: str | None = Field(
        default=None,
        min_length=3,
        max_length=320,
        pattern=_EMAIL_ADDRESS_PATTERN,
    )
    sender_alias: str | None = Field(default=None, min_length=1, max_length=320)
    purpose: EmailMessagePurpose = EmailMessagePurpose.SYSTEM
    subject: str = Field(min_length=1, max_length=1_000)
    body: str = Field(min_length=1, max_length=20_000)
    content_hash: AuditHash
    status: EmailOutboxStatus = EmailOutboxStatus.PENDING
    provider_name: str | None = Field(
        default=None,
        min_length=2,
        max_length=64,
        pattern=_EMAIL_PROVIDER_PATTERN,
    )
    provider_kind: EmailProviderKind | None = None
    route_key: str | None = Field(
        default=None,
        min_length=2,
        max_length=64,
        pattern=_EMAIL_ROUTE_PATTERN,
    )
    provider_ref_hash: str | None = Field(default=None, pattern=_REF_HASH_PATTERN)
    attempt_count: int = Field(default=0, ge=0)
    last_error_code: str | None = Field(default=None, min_length=1, max_length=128)
    correlation_id: CorrelationId
    requested_at: datetime
    updated_at: datetime
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("purpose", "status", "provider_kind", mode="before")
    @classmethod
    def _normalize_enum(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("provider_name", "route_key", mode="before")
    @classmethod
    def _normalize_optional_key(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("requested_at", "updated_at")
    @classmethod
    def _normalize_datetime_fields(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class EmailProviderSendCommand(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    message_id: IdempotencyKey
    tenant_id: TenantId
    route_key: str = Field(min_length=2, max_length=64, pattern=_EMAIL_ROUTE_PATTERN)
    provider_name: str = Field(
        min_length=2,
        max_length=64,
        pattern=_EMAIL_PROVIDER_PATTERN,
    )
    provider_kind: EmailProviderKind
    to_address: str = Field(
        min_length=3, max_length=320, pattern=_EMAIL_ADDRESS_PATTERN
    )
    sender_alias: str | None = Field(default=None, min_length=1, max_length=320)
    subject: str = Field(min_length=1, max_length=1_000)
    body: str = Field(min_length=1, max_length=20_000)
    content_hash: AuditHash
    endpoint_url: str | None = Field(default=None, min_length=1, max_length=2048)
    region: str | None = Field(default=None, min_length=1, max_length=64)
    credentials_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=_SECRET_REF_PATTERN,
    )
    attempt: int = Field(ge=1)
    requested_at: datetime
    correlation_id: CorrelationId
    parameters: dict[str, JSONValue] = Field(default_factory=dict)
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("provider_kind", mode="before")
    @classmethod
    def _normalize_provider_kind(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("requested_at")
    @classmethod
    def _normalize_requested_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class EmailProviderResult(SharedBaseModel):
    provider_name: str = Field(
        min_length=2,
        max_length=64,
        pattern=_EMAIL_PROVIDER_PATTERN,
    )
    provider_ref: str = Field(min_length=1, max_length=512)
    sent_at: datetime

    @field_validator("provider_name", mode="before")
    @classmethod
    def _normalize_provider_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("sent_at")
    @classmethod
    def _normalize_sent_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class EmailProviderAdapter(Protocol):
    async def send(self, command: EmailProviderSendCommand) -> EmailProviderResult:
        """Send an outbox email through a concrete provider adapter."""


@dataclass(slots=True)
class InMemoryEmailProviderAdapter:
    connector_name: str = "in_memory_email"
    fail_with: EmailDeliveryError | None = None
    _commands: list[EmailProviderSendCommand] = field(default_factory=list, init=False)

    @property
    def commands(self) -> tuple[EmailProviderSendCommand, ...]:
        return tuple(self._commands)

    async def send(self, command: EmailProviderSendCommand) -> EmailProviderResult:
        if self.fail_with is not None:
            raise self.fail_with

        self._commands.append(command)
        return EmailProviderResult(
            provider_name=command.provider_name,
            provider_ref=f"{self.connector_name}:{command.message_id}",
            sent_at=command.requested_at,
        )


@dataclass(slots=True)
class InMemoryEmailOutboxRepository:
    _routes: dict[tuple[str, str], EmailProviderRoute] = field(default_factory=dict)
    _messages: dict[tuple[str, str], EmailOutboxMessage] = field(default_factory=dict)

    def upsert_route(self, route: EmailProviderRoute) -> EmailProviderRoute:
        self._routes[(route.tenant_id, route.route_key)] = route
        return route

    def list_routes(
        self,
        *,
        tenant_id: str,
        purpose: EmailMessagePurpose | str | None = None,
    ) -> tuple[EmailProviderRoute, ...]:
        normalized_purpose = (
            _normalize_purpose(purpose) if purpose is not None else None
        )
        routes = (
            route
            for (route_tenant_id, _route_key), route in self._routes.items()
            if route_tenant_id == tenant_id
            and (normalized_purpose is None or route.purpose is normalized_purpose)
        )
        return tuple(
            sorted(routes, key=lambda route: (route.priority, route.route_key))
        )

    def resolve_route(
        self,
        *,
        tenant_id: str,
        purpose: EmailMessagePurpose | str,
    ) -> EmailProviderRoute | None:
        normalized_purpose = _normalize_purpose(purpose)
        for route in self.list_routes(
            tenant_id=tenant_id,
            purpose=normalized_purpose,
        ):
            if route.is_available():
                return route
        return None

    def save_message(self, message: EmailOutboxMessage) -> EmailOutboxMessage:
        self._messages[(message.tenant_id, message.message_id)] = message
        return message

    def get_message(
        self,
        *,
        tenant_id: str,
        message_id: str,
    ) -> EmailOutboxMessage | None:
        return self._messages.get((tenant_id, message_id))

    def list_messages(
        self,
        *,
        tenant_id: str,
        status: EmailOutboxStatus | str | None = None,
    ) -> tuple[EmailOutboxMessage, ...]:
        normalized_status = _normalize_status(status) if status is not None else None
        messages = (
            message
            for (message_tenant_id, _message_id), message in self._messages.items()
            if message_tenant_id == tenant_id
            and (normalized_status is None or message.status is normalized_status)
        )
        return tuple(sorted(messages, key=lambda message: message.requested_at))


@dataclass(slots=True)
class EmailDeliveryService:
    outbox: InMemoryEmailOutboxRepository = field(
        default_factory=InMemoryEmailOutboxRepository
    )
    adapters: dict[str, EmailProviderAdapter] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.adapters = {
            _normalize_provider_name(provider_name): adapter
            for provider_name, adapter in self.adapters.items()
        }

    async def queue_from_notification(
        self,
        command: NotificationEmailCommand,
        *,
        purpose: EmailMessagePurpose | str,
    ) -> EmailOutboxMessage:
        normalized_purpose = _normalize_purpose(purpose)
        requested_at = _normalize_datetime(command.requested_at)
        message = EmailOutboxMessage(
            message_id=_new_id("email"),
            tenant_id=command.tenant_id,
            notification_id=command.notification_id,
            source_event_id=command.event_id,
            source_event_type=command.event_type,
            recipient_id=command.recipient_id,
            to_address=_email_address_from_metadata(
                command.metadata,
                recipient_id=command.recipient_id,
            ),
            purpose=normalized_purpose,
            subject=command.subject,
            body=command.body,
            content_hash=command.content_hash,
            correlation_id=command.correlation_id,
            requested_at=requested_at,
            updated_at=requested_at,
            metadata=_clone_json_object(command.metadata),
        )
        self.outbox.save_message(message)
        return await self.try_send(message)

    async def try_send(self, message: EmailOutboxMessage) -> EmailOutboxMessage:
        if message.to_address is None:
            return self._update_message(
                message,
                status=EmailOutboxStatus.DEFERRED,
                last_error_code="email_recipient_missing",
            )

        route = self.outbox.resolve_route(
            tenant_id=message.tenant_id,
            purpose=message.purpose,
        )
        if route is None:
            return self._update_message(
                message,
                status=EmailOutboxStatus.DEFERRED,
                last_error_code="email_route_unavailable",
            )

        adapter = self.adapters.get(route.provider_name)
        if adapter is None:
            return self._update_message(
                message,
                route=route,
                status=EmailOutboxStatus.DEFERRED,
                last_error_code="email_provider_adapter_missing",
            )

        attempt = message.attempt_count + 1
        send_command = EmailProviderSendCommand(
            message_id=message.message_id,
            tenant_id=message.tenant_id,
            route_key=route.route_key,
            provider_name=route.provider_name,
            provider_kind=route.provider_kind,
            to_address=message.to_address,
            sender_alias=route.sender_alias,
            subject=message.subject,
            body=message.body,
            content_hash=message.content_hash,
            endpoint_url=route.endpoint_url,
            region=route.region,
            credentials_ref=route.credentials_ref,
            attempt=attempt,
            requested_at=message.requested_at,
            correlation_id=message.correlation_id,
            parameters=dict(route.parameters),
            metadata=message.metadata,
        )
        try:
            result = await adapter.send(send_command)
        except EmailDeliveryError as error:
            return self._update_message(
                message,
                route=route,
                status=EmailOutboxStatus.FAILED,
                attempt_count=attempt,
                last_error_code=error.error_code,
            )
        except Exception:
            return self._update_message(
                message,
                route=route,
                status=EmailOutboxStatus.FAILED,
                attempt_count=attempt,
                last_error_code="email_provider_failed",
            )

        return self._update_message(
            message,
            route=route,
            status=EmailOutboxStatus.SENT,
            attempt_count=attempt,
            provider_ref_hash=_hash_ref(
                tenant_id=message.tenant_id,
                value=f"{route.provider_name}:{result.provider_ref}",
            ),
            last_error_code=None,
            updated_at=result.sent_at,
        )

    def _update_message(
        self,
        message: EmailOutboxMessage,
        *,
        status: EmailOutboxStatus,
        route: EmailProviderRoute | None = None,
        attempt_count: int | None = None,
        provider_ref_hash: str | None = None,
        last_error_code: str | None,
        updated_at: datetime | None = None,
    ) -> EmailOutboxMessage:
        updates: dict[str, object] = {
            "status": status,
            "attempt_count": _coalesce(attempt_count, message.attempt_count),
            "last_error_code": last_error_code,
            "updated_at": _normalize_datetime(updated_at or datetime.now(UTC)),
        }
        if route is not None:
            updates.update(
                {
                    "provider_name": route.provider_name,
                    "provider_kind": route.provider_kind,
                    "route_key": route.route_key,
                    "sender_alias": route.sender_alias,
                }
            )
        if provider_ref_hash is not None:
            updates["provider_ref_hash"] = provider_ref_hash

        updated_message = message.model_copy(update=updates)
        self.outbox.save_message(updated_message)
        return updated_message


class NotificationEmailCommand(Protocol):
    notification_id: str
    tenant_id: str
    event_id: str
    event_type: str
    recipient_id: str
    subject: str
    body: str
    content_hash: str
    correlation_id: str
    requested_at: datetime
    metadata: dict[str, JSONValue]


def email_message_purpose_from_metadata(
    metadata: Mapping[str, JSONValue],
) -> EmailMessagePurpose:
    value = metadata.get("message_purpose")
    if isinstance(value, str):
        return _normalize_purpose(value)
    return EmailMessagePurpose.SYSTEM


def _email_address_from_metadata(
    metadata: Mapping[str, JSONValue],
    *,
    recipient_id: str,
) -> str | None:
    direct_email = metadata.get("recipient_email")
    if isinstance(direct_email, str):
        return _normalize_email_or_none(direct_email)

    email_to = metadata.get("email_to")
    if isinstance(email_to, str):
        return _normalize_email_or_none(email_to)

    recipients = metadata.get("email_recipients")
    if isinstance(recipients, dict):
        raw_email = recipients.get(recipient_id)
        if isinstance(raw_email, str):
            return _normalize_email_or_none(raw_email)

    notification_metadata = metadata.get("notification_metadata")
    if isinstance(notification_metadata, dict):
        return _email_address_from_metadata(
            notification_metadata,
            recipient_id=recipient_id,
        )

    return None


def _normalize_email_or_none(value: str) -> str | None:
    normalized = value.strip().lower()
    if normalized == "":
        return None
    if _EMAIL_ADDRESS_RE.fullmatch(normalized) is None:
        return None
    return normalized


def _normalize_provider_name(value: str) -> str:
    return value.strip().lower()


def _normalize_purpose(value: EmailMessagePurpose | str) -> EmailMessagePurpose:
    if isinstance(value, EmailMessagePurpose):
        return value
    return EmailMessagePurpose(value.strip().lower())


def _normalize_status(value: EmailOutboxStatus | str) -> EmailOutboxStatus:
    if isinstance(value, EmailOutboxStatus):
        return value
    return EmailOutboxStatus(value.strip().lower())


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _clone_json_object(value: Mapping[str, JSONValue]) -> dict[str, JSONValue]:
    return dict(value)


def _hash_ref(*, tenant_id: str, value: str) -> str:
    return "sha256:" + hashlib.sha256(f"{tenant_id}:{value}".encode()).hexdigest()


def _coalesce[T](value: T | None, fallback: T) -> T:
    if value is None:
        return fallback
    return value


def _new_id(prefix: str) -> IdempotencyKey:
    return f"{prefix}-{uuid4()}"
