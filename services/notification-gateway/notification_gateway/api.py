from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Protocol, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi import status as http_status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from jinja2 import StrictUndefined, TemplateError, TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import ConfigDict, Field, field_validator

from libs.shared.audit_logger import (
    AuditLogger,
    InMemoryAuditLogSink,
)
from libs.shared.errors import (
    VALIDATION_ERROR_CODE,
    SharedError,
    error_response_body,
)
from libs.shared.events import EventEnvelope, InMemoryEventBus
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
from libs.shared.rbac import (
    BOARD_ROLE,
    COUNCIL_ROLE,
    MEMBER_ASSOC_ROLE,
    MEMBER_FULL_ROLE,
    PRESIDIUM_ROLE,
    AccessPolicy,
    ForbiddenError,
    require_access,
)
from libs.shared.server import (
    BaseAppConfig,
    create_service_runtime_app,
)
from libs.shared.service_template import ServiceTemplateConfig
from libs.shared.tenant import (
    InMemoryAuditSink,
    TenantContext,
    TenantCoreError,
    require_tenant_context,
)
from notification_gateway.email_delivery import (
    EmailDeliveryService,
    EmailMessagePurpose,
    EmailProviderAdapter,
    InMemoryEmailOutboxRepository,
    email_message_purpose_from_metadata,
)

NOTIFICATION_GATEWAY_SERVICE_NAME = "notification-gateway"
NOTIFICATION_GATEWAY_SOURCE = "notification-gateway"
NOTIFICATION_GATEWAY_SCHEMA_VERSION = "1.0"
NOTIFICATION_DISPATCHED_EVENT = "notification.dispatched"
NOTIFICATION_PREFERENCES_UPDATED_EVENT = "notification.preferences.updated"

DEFAULT_NOTIFICATION_CHANNELS = ("telegram",)
DEFAULT_NOTIFICATION_TEMPLATE_KEY = "default_event_notification"

_CHANNEL_PATTERN = r"^[a-z][a-z0-9_-]{1,63}$"
_TEMPLATE_KEY_PATTERN = r"^[a-z][a-z0-9_:-]{1,127}$"
_REF_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"

ChannelName = Annotated[
    str,
    Field(
        min_length=2,
        max_length=64,
        pattern=_CHANNEL_PATTERN,
    ),
]
TemplateKey = Annotated[
    str,
    Field(
        min_length=2,
        max_length=128,
        pattern=_TEMPLATE_KEY_PATTERN,
    ),
]
RecipientIdQuery = Annotated[SubjectId | None, Query(alias="recipient_id")]

NOTIFICATION_SEND_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    action="notification.send",
    resource_type="notification",
)
NOTIFICATION_PREFERENCES_READ_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    MEMBER_FULL_ROLE,
    MEMBER_ASSOC_ROLE,
    action="notification.preferences.read",
    resource_type="notification_preferences",
)
NOTIFICATION_PREFERENCES_WRITE_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    MEMBER_FULL_ROLE,
    MEMBER_ASSOC_ROLE,
    action="notification.preferences.write",
    resource_type="notification_preferences",
)


class NotificationPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    URGENT = "urgent"


class NotificationDeliveryStatus(StrEnum):
    DELIVERED = "delivered"
    FAILED = "failed"


class NotificationGatewayError(ValueError):
    """Raised when a notification cannot be prepared or rendered."""


class NotificationTemplateRequest(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    template_key: TemplateKey
    subject_template: str = Field(min_length=1, max_length=1_000)
    body_template: str = Field(min_length=1, max_length=20_000)
    channels: tuple[ChannelName, ...] = Field(default=DEFAULT_NOTIFICATION_CHANNELS)

    @field_validator("template_key", mode="before")
    @classmethod
    def _normalize_template_key(cls, value: object) -> object:
        return _normalize_lower_token(value)

    @field_validator("channels", mode="before")
    @classmethod
    def _normalize_channels(cls, value: object) -> object:
        return _normalize_string_sequence(value, lower=True)


class NotifyRequest(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    event_id: IdempotencyKey | None = None
    event_type: EventType
    source: str = Field(min_length=1, max_length=128)
    recipients: tuple[SubjectId, ...] = Field(min_length=1)
    channels: tuple[ChannelName, ...] | None = None
    template_key: TemplateKey | None = None
    template: NotificationTemplateRequest | None = None
    context: dict[str, JSONValue] = Field(default_factory=dict)
    message_purpose: EmailMessagePurpose = EmailMessagePurpose.SYSTEM
    priority: NotificationPriority = NotificationPriority.NORMAL
    occurred_at: datetime | None = None
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("event_type", "template_key", mode="before")
    @classmethod
    def _normalize_lower_fields(cls, value: object) -> object:
        return _normalize_lower_token(value)

    @field_validator("source", mode="before")
    @classmethod
    def _normalize_source(cls, value: object) -> object:
        return _normalize_token(value)

    @field_validator("recipients", mode="before")
    @classmethod
    def _normalize_recipients(cls, value: object) -> object:
        return _normalize_string_sequence(value, lower=False)

    @field_validator("channels", mode="before")
    @classmethod
    def _normalize_channels(cls, value: object) -> object:
        if value is None:
            return None
        return _normalize_string_sequence(value, lower=True)

    @field_validator("message_purpose", mode="before")
    @classmethod
    def _normalize_message_purpose(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("occurred_at")
    @classmethod
    def _normalize_optional_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _normalize_datetime(value)


class PreferenceUpdateRequest(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    recipient_id: SubjectId | None = None
    enabled: bool | None = None
    channels: tuple[ChannelName, ...] | None = None
    event_types: tuple[EventType, ...] | None = None
    template_overrides: dict[str, TemplateKey] | None = None

    @field_validator("channels", mode="before")
    @classmethod
    def _normalize_channels(cls, value: object) -> object:
        if value is None:
            return None
        return _normalize_string_sequence(value, lower=True)

    @field_validator("event_types", mode="before")
    @classmethod
    def _normalize_event_types(cls, value: object) -> object:
        if value is None:
            return None
        return _normalize_string_sequence(value, lower=True)

    @field_validator("template_overrides", mode="before")
    @classmethod
    def _normalize_template_overrides(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, dict):
            return value

        normalized: dict[str, object] = {}
        for raw_event_type, raw_template_key in value.items():
            if not isinstance(raw_event_type, str) or not isinstance(
                raw_template_key,
                str,
            ):
                return value
            event_type = raw_event_type.strip().lower()
            template_key = raw_template_key.strip().lower()
            if event_type == "" or template_key == "":
                return value
            normalized[event_type] = template_key
        return normalized


class NotificationTemplate(SharedBaseModel):
    tenant_id: TenantId
    template_key: TemplateKey
    event_type: EventType
    subject_template: str = Field(min_length=1, max_length=1_000)
    body_template: str = Field(min_length=1, max_length=20_000)
    channels: tuple[ChannelName, ...] = Field(default=DEFAULT_NOTIFICATION_CHANNELS)
    revision: int = Field(ge=1)
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def _normalize_updated_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class RecipientPreferences(SharedBaseModel):
    tenant_id: TenantId
    recipient_id: SubjectId
    enabled: bool = True
    channels: tuple[ChannelName, ...] = Field(default=DEFAULT_NOTIFICATION_CHANNELS)
    event_types: tuple[EventType, ...] = Field(default_factory=tuple)
    template_overrides: dict[str, TemplateKey] = Field(default_factory=dict)
    revision: int = Field(ge=1)
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def _normalize_updated_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class NotificationDeliveryCommand(SharedBaseModel):
    notification_id: IdempotencyKey
    tenant_id: TenantId
    event_id: IdempotencyKey
    event_type: EventType
    recipient_id: SubjectId
    recipient_hash: str = Field(pattern=_REF_HASH_PATTERN)
    channel: ChannelName
    template_key: TemplateKey
    subject: str = Field(min_length=1, max_length=1_000)
    body: str = Field(min_length=1, max_length=20_000)
    content_hash: AuditHash
    correlation_id: CorrelationId
    requested_at: datetime
    metadata: dict[str, JSONValue] = Field(default_factory=dict)


class NotificationChannelReceipt(SharedBaseModel):
    channel: ChannelName
    channel_ref_hash: str = Field(pattern=_REF_HASH_PATTERN)
    delivered_at: datetime

    @field_validator("delivered_at")
    @classmethod
    def _normalize_delivered_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class NotificationDeliveryRecord(SharedBaseModel):
    notification_id: IdempotencyKey
    tenant_id: TenantId
    event_id: IdempotencyKey
    event_type: EventType
    recipient_id: SubjectId
    recipient_hash: str = Field(pattern=_REF_HASH_PATTERN)
    channel: ChannelName
    template_key: TemplateKey
    status: NotificationDeliveryStatus
    subject: str = Field(min_length=1, max_length=1_000)
    body: str = Field(min_length=1, max_length=20_000)
    content_hash: AuditHash
    channel_ref_hash: str | None = Field(default=None, pattern=_REF_HASH_PATTERN)
    audit_hash: AuditHash
    delivered_at: datetime

    @field_validator("delivered_at")
    @classmethod
    def _normalize_delivered_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class NotificationDispatchResult(SharedBaseModel):
    notification_id: IdempotencyKey
    tenant_id: TenantId
    event_id: IdempotencyKey
    event_type: EventType
    template_key: TemplateKey
    delivered_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    deliveries: tuple[NotificationDeliveryRecord, ...]


class NotificationChannel(Protocol):
    async def deliver(
        self,
        command: NotificationDeliveryCommand,
    ) -> NotificationChannelReceipt:
        """Deliver a rendered notification through a concrete channel."""


@dataclass(slots=True)
class InMemoryNotificationChannel:
    email_service: EmailDeliveryService | None = None
    _deliveries: list[NotificationDeliveryCommand] = field(default_factory=list)

    @property
    def deliveries(self) -> tuple[NotificationDeliveryCommand, ...]:
        return tuple(self._deliveries)

    async def deliver(
        self,
        command: NotificationDeliveryCommand,
    ) -> NotificationChannelReceipt:
        self._deliveries.append(command)
        channel_ref_subject = f"{command.notification_id}:{command.channel}"
        if command.channel == "email" and self.email_service is not None:
            email_message = await self.email_service.queue_from_notification(
                command,
                purpose=email_message_purpose_from_metadata(command.metadata),
            )
            channel_ref_subject = f"{email_message.message_id}:{email_message.status}"

        return NotificationChannelReceipt(
            channel=command.channel,
            channel_ref_hash=subject_ref_hash(
                tenant_id=command.tenant_id,
                subject_id=channel_ref_subject,
            ),
            delivered_at=command.requested_at,
        )


@dataclass(slots=True)
class InMemoryNotificationRepository:
    _preferences: dict[tuple[str, str], RecipientPreferences] = field(
        default_factory=dict
    )
    _templates: dict[tuple[str, str], NotificationTemplate] = field(
        default_factory=dict
    )

    def get_preferences(
        self,
        *,
        tenant_id: str,
        recipient_id: str,
    ) -> RecipientPreferences | None:
        return self._preferences.get((tenant_id, recipient_id))

    def get_or_default_preferences(
        self,
        *,
        tenant_id: str,
        recipient_id: str,
        now: datetime,
    ) -> RecipientPreferences:
        preferences = self.get_preferences(
            tenant_id=tenant_id,
            recipient_id=recipient_id,
        )
        if preferences is not None:
            return preferences

        return RecipientPreferences(
            tenant_id=tenant_id,
            recipient_id=recipient_id,
            channels=DEFAULT_NOTIFICATION_CHANNELS,
            revision=1,
            updated_at=now,
        )

    def save_preferences(
        self,
        preferences: RecipientPreferences,
    ) -> RecipientPreferences:
        self._preferences[(preferences.tenant_id, preferences.recipient_id)] = (
            preferences
        )
        return preferences

    def upsert_template(
        self,
        *,
        tenant_id: str,
        event_type: str,
        template: NotificationTemplateRequest,
        updated_at: datetime,
    ) -> NotificationTemplate:
        key = tenant_id, template.template_key
        existing = self._templates.get(key)
        revision = 1 if existing is None else existing.revision + 1
        record = NotificationTemplate(
            tenant_id=tenant_id,
            template_key=template.template_key,
            event_type=event_type,
            subject_template=template.subject_template,
            body_template=template.body_template,
            channels=template.channels,
            revision=revision,
            updated_at=updated_at,
        )
        self._templates[key] = record
        return record

    def get_template(
        self,
        *,
        tenant_id: str,
        template_key: str,
    ) -> NotificationTemplate | None:
        return self._templates.get((tenant_id, template_key))

    def find_template_for_event(
        self,
        *,
        tenant_id: str,
        event_type: str,
    ) -> NotificationTemplate | None:
        templates = (
            template
            for (template_tenant_id, _template_key), template in self._templates.items()
            if template_tenant_id == tenant_id and template.event_type == event_type
        )
        return next(
            iter(
                sorted(
                    templates,
                    key=lambda template: template.updated_at,
                    reverse=True,
                )
            ),
            None,
        )


@dataclass(slots=True)
class NotificationGateway:
    repository: InMemoryNotificationRepository = field(
        default_factory=InMemoryNotificationRepository
    )
    channel: NotificationChannel = field(default_factory=InMemoryNotificationChannel)
    publisher: InMemoryEventBus = field(default_factory=InMemoryEventBus)
    audit_logger: AuditLogger = field(default_factory=AuditLogger)

    async def update_preferences(
        self,
        *,
        tenant_id: str,
        recipient_id: str,
        updated_by: str,
        correlation_id: str,
        update: PreferenceUpdateRequest,
        updated_at: datetime | None = None,
    ) -> RecipientPreferences:
        changed_at = _normalize_datetime(updated_at or datetime.now(UTC))
        existing = self.repository.get_or_default_preferences(
            tenant_id=tenant_id,
            recipient_id=recipient_id,
            now=changed_at,
        )
        preferences = RecipientPreferences(
            tenant_id=tenant_id,
            recipient_id=recipient_id,
            enabled=_coalesce(update.enabled, existing.enabled),
            channels=_coalesce(update.channels, existing.channels),
            event_types=_coalesce(update.event_types, existing.event_types),
            template_overrides=_coalesce(
                update.template_overrides,
                existing.template_overrides,
            ),
            revision=(
                existing.revision + 1
                if self.repository.get_preferences(
                    tenant_id=tenant_id,
                    recipient_id=recipient_id,
                )
                is not None
                else existing.revision
            ),
            updated_at=changed_at,
        )
        self.repository.save_preferences(preferences)

        recipient_hash = subject_ref_hash(
            tenant_id=tenant_id,
            subject_id=recipient_id,
        )
        audit_record = self.audit_logger.record(
            event_type=NOTIFICATION_PREFERENCES_UPDATED_EVENT,
            tenant_id=tenant_id,
            metadata={
                "recipient_hash": recipient_hash,
                "channels": list(preferences.channels),
                "event_types": list(preferences.event_types),
                "revision": preferences.revision,
            },
            timestamp=changed_at,
            correlation_id=correlation_id,
            actor_hash=subject_ref_hash(tenant_id=tenant_id, subject_id=updated_by),
            source=NOTIFICATION_GATEWAY_SOURCE,
        )
        await self.publisher.publish(
            EventEnvelope(
                event_id=_new_id("evt-notification-preferences-updated"),
                type=NOTIFICATION_PREFERENCES_UPDATED_EVENT,
                schema_version=NOTIFICATION_GATEWAY_SCHEMA_VERSION,
                tenant_id=tenant_id,
                source=NOTIFICATION_GATEWAY_SOURCE,
                correlation_id=correlation_id,
                occurred_at=changed_at,
                payload={
                    "recipient_hash": recipient_hash,
                    "channels": list(preferences.channels),
                    "event_types": list(preferences.event_types),
                    "revision": preferences.revision,
                    "audit_hash": audit_record.audit_hash,
                },
            )
        )
        return preferences

    async def dispatch(
        self,
        *,
        tenant_id: str,
        payload: NotifyRequest,
        actor_id: str | None,
        correlation_id: str,
    ) -> NotificationDispatchResult:
        occurred_at = _normalize_datetime(payload.occurred_at or datetime.now(UTC))
        event_id = payload.event_id or _new_id("evt-notification-source")
        notification_id = _new_id("notification")
        base_template = self._resolve_base_template(
            tenant_id=tenant_id,
            payload=payload,
            occurred_at=occurred_at,
        )
        deliveries: list[NotificationDeliveryRecord] = []
        failed_count = 0
        skipped_count = 0

        for recipient_id in payload.recipients:
            preferences = self.repository.get_or_default_preferences(
                tenant_id=tenant_id,
                recipient_id=recipient_id,
                now=occurred_at,
            )
            if not preferences.enabled or not _subscribes_to_event(
                preferences,
                payload.event_type,
            ):
                skipped_count += 1
                continue

            template = self._template_for_recipient(
                tenant_id=tenant_id,
                event_type=payload.event_type,
                preferences=preferences,
                fallback=base_template,
            )
            channels = _delivery_channels(
                requested_channels=payload.channels,
                template_channels=template.channels,
                preference_channels=preferences.channels,
            )
            if not channels:
                skipped_count += 1
                continue

            rendered = _render_notification(
                template=template,
                payload=payload,
                tenant_id=tenant_id,
                recipient_id=recipient_id,
                event_id=event_id,
            )
            for channel_name in channels:
                delivery = await self._deliver(
                    notification_id=f"{notification_id}-{len(deliveries) + 1}",
                    tenant_id=tenant_id,
                    event_id=event_id,
                    payload=payload,
                    actor_id=actor_id,
                    correlation_id=correlation_id,
                    occurred_at=occurred_at,
                    recipient_id=recipient_id,
                    channel_name=channel_name,
                    template=template,
                    rendered=rendered,
                )
                if delivery.status is NotificationDeliveryStatus.FAILED:
                    failed_count += 1
                deliveries.append(delivery)

        result = NotificationDispatchResult(
            notification_id=notification_id,
            tenant_id=tenant_id,
            event_id=event_id,
            event_type=payload.event_type,
            template_key=base_template.template_key,
            delivered_count=sum(
                1
                for delivery in deliveries
                if delivery.status is NotificationDeliveryStatus.DELIVERED
            ),
            skipped_count=skipped_count,
            failed_count=failed_count,
            deliveries=tuple(deliveries),
        )
        await self.publisher.publish(
            _dispatch_event(
                result=result,
                correlation_id=correlation_id,
                occurred_at=occurred_at,
                metadata=payload.metadata,
            )
        )
        return result

    def _resolve_base_template(
        self,
        *,
        tenant_id: str,
        payload: NotifyRequest,
        occurred_at: datetime,
    ) -> NotificationTemplate:
        if payload.template is not None:
            return self.repository.upsert_template(
                tenant_id=tenant_id,
                event_type=payload.event_type,
                template=payload.template,
                updated_at=occurred_at,
            )
        if payload.template_key is not None:
            template = self.repository.get_template(
                tenant_id=tenant_id,
                template_key=payload.template_key,
            )
            if template is not None:
                return template

        template = self.repository.find_template_for_event(
            tenant_id=tenant_id,
            event_type=payload.event_type,
        )
        if template is not None:
            return template

        return _default_template(
            tenant_id=tenant_id,
            event_type=payload.event_type,
            occurred_at=occurred_at,
        )

    def _template_for_recipient(
        self,
        *,
        tenant_id: str,
        event_type: str,
        preferences: RecipientPreferences,
        fallback: NotificationTemplate,
    ) -> NotificationTemplate:
        override_key = preferences.template_overrides.get(event_type)
        if override_key is None:
            return fallback

        template = self.repository.get_template(
            tenant_id=tenant_id,
            template_key=override_key,
        )
        return template or fallback

    async def _deliver(
        self,
        *,
        notification_id: str,
        tenant_id: str,
        event_id: str,
        payload: NotifyRequest,
        actor_id: str | None,
        correlation_id: str,
        occurred_at: datetime,
        recipient_id: str,
        channel_name: str,
        template: NotificationTemplate,
        rendered: tuple[str, str, str],
    ) -> NotificationDeliveryRecord:
        subject, body, content_hash = rendered
        recipient_hash = subject_ref_hash(tenant_id=tenant_id, subject_id=recipient_id)
        audit_record = self.audit_logger.record(
            event_type=NOTIFICATION_DISPATCHED_EVENT,
            tenant_id=tenant_id,
            metadata={
                "event_id": event_id,
                "event_type": payload.event_type,
                "recipient_hash": recipient_hash,
                "channel": channel_name,
                "template_key": template.template_key,
                "content_hash": content_hash,
                "priority": payload.priority.value,
                "metadata": payload.metadata,
            },
            timestamp=occurred_at,
            correlation_id=correlation_id,
            actor_hash=(
                subject_ref_hash(tenant_id=tenant_id, subject_id=actor_id)
                if actor_id is not None
                else None
            ),
            source=NOTIFICATION_GATEWAY_SOURCE,
        )
        command = NotificationDeliveryCommand(
            notification_id=notification_id,
            tenant_id=tenant_id,
            event_id=event_id,
            event_type=payload.event_type,
            recipient_id=recipient_id,
            recipient_hash=recipient_hash,
            channel=channel_name,
            template_key=template.template_key,
            subject=subject,
            body=body,
            content_hash=content_hash,
            correlation_id=correlation_id,
            requested_at=occurred_at,
            metadata={
                "source": payload.source,
                "priority": payload.priority.value,
                "message_purpose": payload.message_purpose.value,
                "audit_hash": audit_record.audit_hash,
                "notification_metadata": dict(payload.metadata),
                "recipient_email": _email_recipient_address(
                    payload=payload,
                    recipient_id=recipient_id,
                ),
            },
        )
        try:
            receipt = await self.channel.deliver(command)
        except Exception:
            return NotificationDeliveryRecord(
                notification_id=notification_id,
                tenant_id=tenant_id,
                event_id=event_id,
                event_type=payload.event_type,
                recipient_id=recipient_id,
                recipient_hash=recipient_hash,
                channel=channel_name,
                template_key=template.template_key,
                status=NotificationDeliveryStatus.FAILED,
                subject=subject,
                body=body,
                content_hash=content_hash,
                audit_hash=audit_record.audit_hash,
                delivered_at=occurred_at,
            )

        return NotificationDeliveryRecord(
            notification_id=notification_id,
            tenant_id=tenant_id,
            event_id=event_id,
            event_type=payload.event_type,
            recipient_id=recipient_id,
            recipient_hash=recipient_hash,
            channel=channel_name,
            template_key=template.template_key,
            status=NotificationDeliveryStatus.DELIVERED,
            subject=subject,
            body=body,
            content_hash=content_hash,
            channel_ref_hash=receipt.channel_ref_hash,
            audit_hash=audit_record.audit_hash,
            delivered_at=receipt.delivered_at,
        )


@dataclass(slots=True)
class NotificationGatewayAPIState:
    gateway: NotificationGateway
    repository: InMemoryNotificationRepository
    channel: InMemoryNotificationChannel
    email_outbox: InMemoryEmailOutboxRepository
    email_service: EmailDeliveryService
    publisher: InMemoryEventBus
    audit_log_sink: InMemoryAuditLogSink
    tenant_audit_sink: InMemoryAuditSink


router = APIRouter(tags=["Notification Gateway"])


def create_notification_gateway_app(
    config: BaseAppConfig | ServiceTemplateConfig,
    *,
    repository: InMemoryNotificationRepository | None = None,
    channel: InMemoryNotificationChannel | None = None,
    email_outbox: InMemoryEmailOutboxRepository | None = None,
    email_service: EmailDeliveryService | None = None,
    email_adapters: Mapping[str, EmailProviderAdapter] | None = None,
    publisher: InMemoryEventBus | None = None,
    audit_log_sink: InMemoryAuditLogSink | None = None,
    tenant_audit_sink: InMemoryAuditSink | None = None,
) -> FastAPI:
    resolved_repository = repository or InMemoryNotificationRepository()
    if email_service is not None:
        resolved_email_service = email_service
        resolved_email_outbox = email_outbox or email_service.outbox
    else:
        resolved_email_outbox = email_outbox or InMemoryEmailOutboxRepository()
        resolved_email_service = EmailDeliveryService(
            outbox=resolved_email_outbox,
            adapters=dict(email_adapters or {}),
        )
    resolved_channel = channel or InMemoryNotificationChannel(
        email_service=resolved_email_service,
    )
    if resolved_channel.email_service is None:
        resolved_channel.email_service = resolved_email_service
    resolved_publisher = publisher or InMemoryEventBus()
    resolved_audit_log_sink = audit_log_sink or InMemoryAuditLogSink()
    resolved_tenant_audit_sink = tenant_audit_sink or InMemoryAuditSink()
    gateway = NotificationGateway(
        repository=resolved_repository,
        channel=resolved_channel,
        publisher=resolved_publisher,
        audit_logger=AuditLogger(sink=resolved_audit_log_sink),
    )
    app = create_service_runtime_app(
        config,
        title="Media Center Notification Gateway",
        audit_sink=resolved_tenant_audit_sink,
    )
    app.state.notification_gateway_api = NotificationGatewayAPIState(
        gateway=gateway,
        repository=resolved_repository,
        channel=resolved_channel,
        email_outbox=resolved_email_outbox,
        email_service=resolved_email_service,
        publisher=resolved_publisher,
        audit_log_sink=resolved_audit_log_sink,
        tenant_audit_sink=resolved_tenant_audit_sink,
    )
    app.add_exception_handler(TenantCoreError, _tenant_core_error_handler)
    app.add_exception_handler(SharedError, _shared_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(NotificationGatewayError, _notification_error_handler)
    app.add_exception_handler(ValueError, _value_error_handler)
    app.include_router(router)
    return app


@router.post(
    "/notify",
    response_model=NotificationDispatchResult,
    status_code=http_status.HTTP_202_ACCEPTED,
    summary="Отправить уведомления по событию",
)
async def notify(
    payload: NotifyRequest,
    state: Annotated[NotificationGatewayAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> NotificationDispatchResult:
    actor_context = require_access(NOTIFICATION_SEND_POLICY, context=context)
    return await state.gateway.dispatch(
        tenant_id=context.tenant_id,
        payload=payload,
        actor_id=actor_context.subject,
        correlation_id=_correlation_id(context),
    )


@router.get(
    "/notify/preferences",
    response_model=RecipientPreferences,
    summary="Получить настройки уведомлений получателя",
)
def get_preferences(
    state: Annotated[NotificationGatewayAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
    recipient_id: RecipientIdQuery = None,
) -> RecipientPreferences:
    actor_context = require_access(
        NOTIFICATION_PREFERENCES_READ_POLICY,
        context=context,
    )
    resolved_recipient_id = _managed_recipient_id(recipient_id, actor_context)
    return state.repository.get_or_default_preferences(
        tenant_id=context.tenant_id,
        recipient_id=resolved_recipient_id,
        now=datetime.now(UTC),
    )


@router.put(
    "/notify/preferences",
    response_model=RecipientPreferences,
    summary="Обновить настройки уведомлений получателя",
)
async def update_preferences(
    payload: PreferenceUpdateRequest,
    state: Annotated[NotificationGatewayAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> RecipientPreferences:
    actor_context = require_access(
        NOTIFICATION_PREFERENCES_WRITE_POLICY,
        context=context,
    )
    resolved_recipient_id = _managed_recipient_id(
        payload.recipient_id,
        actor_context,
    )
    return await state.gateway.update_preferences(
        tenant_id=context.tenant_id,
        recipient_id=resolved_recipient_id,
        updated_by=_subject(actor_context),
        correlation_id=_correlation_id(context),
        update=payload,
    )


def subject_ref_hash(*, tenant_id: str, subject_id: str) -> str:
    return "sha256:" + hashlib.sha256(f"{tenant_id}:{subject_id}".encode()).hexdigest()


def _api_state(request: Request) -> NotificationGatewayAPIState:
    return cast(
        NotificationGatewayAPIState,
        request.app.state.notification_gateway_api,
    )


def _tenant_context() -> TenantContext:
    return require_tenant_context()


def _subject(context: TenantContext) -> SubjectId:
    if context.subject is None or context.subject.strip() == "":
        raise SharedError(
            status_code=400,
            error_code="actor_required",
            message="Операция Notification Gateway требует subject в tenant context",
            correlation_id=context.correlation_id,
        )
    return context.subject


def _managed_recipient_id(
    requested_recipient_id: str | None,
    context: TenantContext,
) -> str:
    subject = _subject(context)
    recipient_id = requested_recipient_id or subject
    if recipient_id == subject:
        return recipient_id

    if frozenset(context.roles).intersection(
        {COUNCIL_ROLE, PRESIDIUM_ROLE, BOARD_ROLE}
    ):
        return recipient_id

    raise ForbiddenError(
        "Участник может изменять только собственные настройки уведомлений",
        details={"recipient_id": recipient_id},
        correlation_id=context.correlation_id,
    )


def _correlation_id(context: TenantContext) -> CorrelationId:
    return context.correlation_id or f"corr-{uuid4()}"


def _render_notification(
    *,
    template: NotificationTemplate,
    payload: NotifyRequest,
    tenant_id: str,
    recipient_id: str,
    event_id: str,
) -> tuple[str, str, str]:
    context = dict(payload.context)
    context.update(
        {
            "tenant_id": tenant_id,
            "recipient_id": recipient_id,
            "event_id": event_id,
            "event_type": payload.event_type,
            "source": payload.source,
            "priority": payload.priority.value,
        }
    )
    subject = _render_template(template.subject_template, context)
    body = _render_template(template.body_template, context)
    return subject, body, _content_hash(subject=subject, body=body)


def _render_template(template_body: str, context: Mapping[str, JSONValue]) -> str:
    environment = _template_environment()
    try:
        template = environment.from_string(template_body)
        rendered = template.render(context).strip()
    except (TemplateSyntaxError, TemplateError) as exc:
        raise NotificationGatewayError(str(exc)) from exc

    if rendered == "":
        raise NotificationGatewayError("Шаблон уведомления вернул пустой результат")
    return rendered


def _template_environment() -> SandboxedEnvironment:
    environment = SandboxedEnvironment(
        autoescape=False,
        lstrip_blocks=True,
        trim_blocks=True,
        undefined=StrictUndefined,
    )
    environment.globals.clear()
    return environment


def _content_hash(*, subject: str, body: str) -> str:
    return hashlib.sha256(f"{subject}\n{body}".encode()).hexdigest()


def _dispatch_event(
    *,
    result: NotificationDispatchResult,
    correlation_id: str,
    occurred_at: datetime,
    metadata: Mapping[str, JSONValue],
) -> EventEnvelope:
    delivered = tuple(
        delivery
        for delivery in result.deliveries
        if delivery.status is NotificationDeliveryStatus.DELIVERED
    )
    channels: list[JSONValue] = []
    delivery_ids: list[JSONValue] = []
    recipient_hashes: list[JSONValue] = []
    for delivery in delivered:
        channels.append(delivery.channel)
        delivery_ids.append(delivery.notification_id)
    for recipient_hash in sorted({delivery.recipient_hash for delivery in delivered}):
        recipient_hashes.append(recipient_hash)

    payload: dict[str, JSONValue] = {
        "notification_id": result.notification_id,
        "source_event_id": result.event_id,
        "source_event_type": result.event_type,
        "template_key": result.template_key,
        "delivered_count": result.delivered_count,
        "skipped_count": result.skipped_count,
        "failed_count": result.failed_count,
        "channels": channels,
        "delivery_ids": delivery_ids,
        "recipient_hashes": recipient_hashes,
        "metadata": dict(metadata),
    }
    return EventEnvelope(
        event_id=_new_id("evt-notification-dispatched"),
        type=NOTIFICATION_DISPATCHED_EVENT,
        schema_version=NOTIFICATION_GATEWAY_SCHEMA_VERSION,
        tenant_id=result.tenant_id,
        source=NOTIFICATION_GATEWAY_SOURCE,
        correlation_id=correlation_id,
        occurred_at=occurred_at,
        payload=payload,
        causation_id=result.event_id,
    )


def _default_template(
    *,
    tenant_id: str,
    event_type: str,
    occurred_at: datetime,
) -> NotificationTemplate:
    return NotificationTemplate(
        tenant_id=tenant_id,
        template_key=DEFAULT_NOTIFICATION_TEMPLATE_KEY,
        event_type=event_type,
        subject_template="Событие {{ event_type }}",
        body_template="Источник {{ source }} отправил событие {{ event_id }}",
        channels=DEFAULT_NOTIFICATION_CHANNELS,
        revision=1,
        updated_at=occurred_at,
    )


def _delivery_channels(
    *,
    requested_channels: tuple[str, ...] | None,
    template_channels: tuple[str, ...],
    preference_channels: tuple[str, ...],
) -> tuple[str, ...]:
    base_channels = (
        requested_channels or template_channels or DEFAULT_NOTIFICATION_CHANNELS
    )
    allowed = set(preference_channels)
    return tuple(channel for channel in base_channels if channel in allowed)


def _subscribes_to_event(
    preferences: RecipientPreferences,
    event_type: str,
) -> bool:
    return not preferences.event_types or event_type in preferences.event_types


def _email_recipient_address(
    *,
    payload: NotifyRequest,
    recipient_id: str,
) -> str | None:
    metadata_address = _email_recipient_address_from_mapping(
        payload.metadata,
        recipient_id=recipient_id,
    )
    if metadata_address is not None:
        return metadata_address

    return _email_recipient_address_from_mapping(
        payload.context,
        recipient_id=recipient_id,
    )


def _email_recipient_address_from_mapping(
    value: Mapping[str, JSONValue],
    *,
    recipient_id: str,
) -> str | None:
    direct_email = value.get("recipient_email")
    if isinstance(direct_email, str):
        return direct_email

    email_to = value.get("email_to")
    if isinstance(email_to, str):
        return email_to

    email_recipients = value.get("email_recipients")
    if isinstance(email_recipients, dict):
        raw_email = email_recipients.get(recipient_id)
        if isinstance(raw_email, str):
            return raw_email

    return None


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _normalize_string_sequence(value: object, *, lower: bool) -> object:
    if isinstance(value, str):
        raw_items: Sequence[object] = (value,)
    elif isinstance(value, Sequence) and not isinstance(
        value,
        bytes | bytearray,
    ):
        raw_items = value
    else:
        return value

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, str):
            return value
        item = raw_item.strip()
        if lower:
            item = item.lower()
        if item == "":
            return value
        if item in seen:
            continue
        normalized.append(item)
        seen.add(item)
    return tuple(normalized)


def _normalize_lower_token(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower()
    return value


def _normalize_token(value: object) -> object:
    if isinstance(value, str):
        return value.strip()
    return value


def _coalesce[T](value: T | None, fallback: T) -> T:
    if value is None:
        return fallback
    return value


def _new_id(prefix: str) -> IdempotencyKey:
    return f"{prefix}-{uuid4()}"


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


async def _notification_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=error_response_body(
            code="notification_gateway_error",
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
