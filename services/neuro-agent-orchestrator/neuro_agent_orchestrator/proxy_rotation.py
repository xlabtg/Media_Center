from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from pydantic import ConfigDict, Field, field_validator, model_validator

from libs.shared.audit_logger import AuditLogger
from libs.shared.events import EventEnvelope, EventPublisher, InMemoryEventBus
from libs.shared.models import (
    AuditHash,
    IdempotencyKey,
    JSONValue,
    SharedBaseModel,
    SubjectId,
    TenantId,
)

PROXY_ROTATION_SOURCE = "neuro-agent-orchestrator"
PROXY_ROTATION_SCHEMA_VERSION = "1.0"
PROXY_POOL_UPDATED_EVENT = "neuro_agent.proxy_pool.updated"
PROXY_HEALTH_CHECKED_EVENT = "neuro_agent.proxy_health.checked"
PROXY_LEASED_EVENT = "neuro_agent.proxy.leased"
DEFAULT_PROXY_AUDIT_HASH = "0" * 64

_PROXY_URL_MAX_LENGTH = 2_048
_PLATFORM_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"
_REASON_CODE_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
_SECRET_REF_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"
_HTTP_SCHEMES = frozenset({"http", "https"})
_SOCKS5_SCHEMES = frozenset({"socks5", "socks5h"})
_MTPROTO_SCHEMES = frozenset({"mtproto"})


class ProxyRotationError(RuntimeError):
    """Base error for tenant-scoped proxy rotation failures."""


class ProxyPoolNotFoundError(ProxyRotationError):
    """Raised when a tenant has no requested proxy pool."""


class ProxyUnavailableError(ProxyRotationError):
    """Raised when a pool has no live proxy to lease."""


class ProxyProtocol(StrEnum):
    HTTP = "http"
    SOCKS5 = "socks5"
    MTPROTO = "mtproto"


class ProxyHealthStatus(StrEnum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DISABLED = "disabled"


class ProxyRotationStrategy(StrEnum):
    ROUND_ROBIN = "round_robin"


class ProxyEndpointConfig(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    proxy_id: IdempotencyKey
    protocol: ProxyProtocol
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
    def _validate_protocol_scheme(self) -> ProxyEndpointConfig:
        scheme = urlsplit(self.url).scheme.lower()
        if self.protocol is ProxyProtocol.HTTP and scheme not in _HTTP_SCHEMES:
            raise ValueError("HTTP proxy должен использовать схему http или https")
        if self.protocol is ProxyProtocol.SOCKS5 and scheme not in _SOCKS5_SCHEMES:
            raise ValueError(
                "SOCKS5 proxy должен использовать схему socks5 или socks5h"
            )
        if self.protocol is ProxyProtocol.MTPROTO and scheme not in _MTPROTO_SCHEMES:
            raise ValueError("MTProto proxy должен использовать схему mtproto")

        return self


class ProxyEndpointState(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    proxy_id: IdempotencyKey
    protocol: ProxyProtocol
    redacted_url: str = Field(min_length=1, max_length=_PROXY_URL_MAX_LENGTH)
    url_hash: str = Field(
        min_length=71, max_length=71, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    secret_ref_hash: str | None = Field(
        default=None,
        min_length=71,
        max_length=71,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    priority: int = Field(ge=0, le=10_000)
    enabled: bool
    health_status: ProxyHealthStatus
    consecutive_failures: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    last_checked_at: datetime | None = None
    last_latency_ms: int | None = Field(default=None, ge=0)
    last_failure_reason: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=_REASON_CODE_PATTERN,
    )
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("last_checked_at")
    @classmethod
    def _normalize_last_checked_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None

        return normalize_datetime(value)

    def is_leasable(self) -> bool:
        return self.enabled and self.health_status is ProxyHealthStatus.HEALTHY


class ProxyPoolState(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    tenant_id: TenantId
    pool_id: IdempotencyKey
    platform: str = Field(pattern=_PLATFORM_PATTERN)
    rotation_strategy: ProxyRotationStrategy
    revision: int = Field(ge=1)
    rotation_cursor: int = Field(ge=0)
    total_proxy_count: int = Field(ge=1)
    healthy_proxy_count: int = Field(ge=0)
    unhealthy_proxy_count: int = Field(ge=0)
    disabled_proxy_count: int = Field(ge=0)
    proxies: tuple[ProxyEndpointState, ...] = Field(min_length=1)
    updated_by: SubjectId
    updated_at: datetime
    audit_hash: AuditHash = DEFAULT_PROXY_AUDIT_HASH
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("updated_at")
    @classmethod
    def _normalize_updated_at(cls, value: datetime) -> datetime:
        return normalize_datetime(value)


class ProxyHealthSignal(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    proxy_id: IdempotencyKey
    alive: bool
    checked_at: datetime | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    reason_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=_REASON_CODE_PATTERN,
    )

    @field_validator("checked_at")
    @classmethod
    def _normalize_checked_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None

        return normalize_datetime(value)


class ProxyHealthCheckResult(SharedBaseModel):
    tenant_id: TenantId
    pool_id: IdempotencyKey
    checked_proxy_count: int = Field(ge=1)
    healthy_proxy_count: int = Field(ge=0)
    unhealthy_proxy_count: int = Field(ge=0)
    audit_hash: AuditHash
    pool: ProxyPoolState


class ProxyLease(SharedBaseModel):
    tenant_id: TenantId
    pool_id: IdempotencyKey
    lease_id: IdempotencyKey
    proxy_id: IdempotencyKey
    protocol: ProxyProtocol
    redacted_url: str = Field(min_length=1, max_length=_PROXY_URL_MAX_LENGTH)
    url_hash: str = Field(
        min_length=71, max_length=71, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    secret_ref_hash: str | None = Field(
        default=None,
        min_length=71,
        max_length=71,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    health_status: ProxyHealthStatus
    pool_revision: int = Field(ge=1)
    rotation_strategy: ProxyRotationStrategy
    selected_at: datetime
    audit_hash: AuditHash

    @field_validator("selected_at")
    @classmethod
    def _normalize_selected_at(cls, value: datetime) -> datetime:
        return normalize_datetime(value)


@dataclass(slots=True)
class InMemoryProxyPoolRepository:
    _pools: dict[tuple[str, str], ProxyPoolState] = field(default_factory=dict)

    def get_pool(self, *, tenant_id: str, pool_id: str) -> ProxyPoolState | None:
        return self._pools.get((tenant_id, pool_id))

    def require_pool(self, *, tenant_id: str, pool_id: str) -> ProxyPoolState:
        pool = self.get_pool(tenant_id=tenant_id, pool_id=pool_id)
        if pool is None:
            raise ProxyPoolNotFoundError("Прокси-пул не найден для tenant")

        return pool

    def save_pool(self, pool: ProxyPoolState) -> ProxyPoolState:
        self._pools[(pool.tenant_id, pool.pool_id)] = pool
        return pool


@dataclass(slots=True)
class ProxyRotationManager:
    publisher: EventPublisher = field(default_factory=InMemoryEventBus)
    repository: InMemoryProxyPoolRepository = field(
        default_factory=InMemoryProxyPoolRepository
    )
    audit_logger: AuditLogger = field(default_factory=AuditLogger)

    async def upsert_pool(
        self,
        *,
        tenant_id: str,
        pool_id: str,
        platform: str,
        proxies: tuple[ProxyEndpointConfig, ...],
        updated_by: str,
        correlation_id: str,
        rotation_strategy: ProxyRotationStrategy = ProxyRotationStrategy.ROUND_ROBIN,
        metadata: Mapping[str, JSONValue] | None = None,
        updated_at: datetime | str | None = None,
        event_id: str | None = None,
    ) -> ProxyPoolState:
        if len(proxies) == 0:
            raise ProxyRotationError("Прокси-пул должен содержать хотя бы один proxy")

        existing = self.repository.get_pool(tenant_id=tenant_id, pool_id=pool_id)
        changed_at = normalize_datetime(updated_at or datetime.now(UTC))
        proxy_states = tuple(_state_from_config(proxy) for proxy in proxies)
        _ensure_unique_proxy_ids(proxy_states)
        pool = build_proxy_pool_state(
            tenant_id=tenant_id,
            pool_id=pool_id,
            platform=platform,
            rotation_strategy=rotation_strategy,
            revision=1 if existing is None else existing.revision + 1,
            rotation_cursor=0,
            proxies=proxy_states,
            updated_by=updated_by,
            updated_at=changed_at,
            metadata=metadata or {},
        )
        audit_record = self.audit_logger.record(
            event_type=PROXY_POOL_UPDATED_EVENT,
            tenant_id=tenant_id,
            metadata=_pool_audit_metadata(pool),
            timestamp=changed_at,
            correlation_id=correlation_id,
            actor_hash=subject_ref_hash(tenant_id=tenant_id, subject_id=updated_by),
            source=PROXY_ROTATION_SOURCE,
        )
        pool = pool.model_copy(update={"audit_hash": audit_record.audit_hash})
        self.repository.save_pool(pool)

        await self.publisher.publish(
            EventEnvelope(
                event_id=event_id or _new_id("evt-proxy-pool-updated"),
                type=PROXY_POOL_UPDATED_EVENT,
                schema_version=PROXY_ROTATION_SCHEMA_VERSION,
                tenant_id=tenant_id,
                source=PROXY_ROTATION_SOURCE,
                correlation_id=correlation_id,
                occurred_at=changed_at,
                payload={
                    "pool_id": pool.pool_id,
                    "platform": pool.platform,
                    "revision": pool.revision,
                    "total_proxy_count": pool.total_proxy_count,
                    "healthy_proxy_count": pool.healthy_proxy_count,
                    "unhealthy_proxy_count": pool.unhealthy_proxy_count,
                    "protocols": _json_string_list(
                        sorted({proxy.protocol.value for proxy in pool.proxies})
                    ),
                    "audit_hash": pool.audit_hash,
                },
            )
        )
        return pool

    def get_pool(self, *, tenant_id: str, pool_id: str) -> ProxyPoolState:
        return self.repository.require_pool(tenant_id=tenant_id, pool_id=pool_id)

    async def check_pool_health(
        self,
        *,
        tenant_id: str,
        pool_id: str,
        checks: tuple[ProxyHealthSignal, ...],
        checked_by: str,
        correlation_id: str,
        event_id: str | None = None,
    ) -> ProxyHealthCheckResult:
        if len(checks) == 0:
            raise ProxyRotationError("checks должен содержать хотя бы один результат")

        pool = self.repository.require_pool(tenant_id=tenant_id, pool_id=pool_id)
        checked_pool = apply_proxy_health_checks(
            pool=pool,
            checks=checks,
            updated_by=checked_by,
            updated_at=max(
                (check.checked_at for check in checks if check.checked_at is not None),
                default=datetime.now(UTC),
            ),
        )
        audit_record = self.audit_logger.record(
            event_type=PROXY_HEALTH_CHECKED_EVENT,
            tenant_id=tenant_id,
            metadata={
                **_pool_audit_metadata(checked_pool),
                "checked_proxy_count": len(checks),
                "checked_proxy_ids": [check.proxy_id for check in checks],
                "failed_proxy_ids": [
                    check.proxy_id for check in checks if not check.alive
                ],
            },
            timestamp=checked_pool.updated_at,
            correlation_id=correlation_id,
            actor_hash=subject_ref_hash(tenant_id=tenant_id, subject_id=checked_by),
            source=PROXY_ROTATION_SOURCE,
        )
        checked_pool = checked_pool.model_copy(
            update={"audit_hash": audit_record.audit_hash}
        )
        self.repository.save_pool(checked_pool)

        await self.publisher.publish(
            EventEnvelope(
                event_id=event_id or _new_id("evt-proxy-health-checked"),
                type=PROXY_HEALTH_CHECKED_EVENT,
                schema_version=PROXY_ROTATION_SCHEMA_VERSION,
                tenant_id=tenant_id,
                source=PROXY_ROTATION_SOURCE,
                correlation_id=correlation_id,
                occurred_at=checked_pool.updated_at,
                payload={
                    "pool_id": checked_pool.pool_id,
                    "revision": checked_pool.revision,
                    "checked_proxy_count": len(checks),
                    "healthy_proxy_count": checked_pool.healthy_proxy_count,
                    "unhealthy_proxy_count": checked_pool.unhealthy_proxy_count,
                    "failed_proxy_ids": [
                        check.proxy_id for check in checks if not check.alive
                    ],
                    "audit_hash": audit_record.audit_hash,
                },
            )
        )
        return ProxyHealthCheckResult(
            tenant_id=tenant_id,
            pool_id=pool_id,
            checked_proxy_count=len(checks),
            healthy_proxy_count=checked_pool.healthy_proxy_count,
            unhealthy_proxy_count=checked_pool.unhealthy_proxy_count,
            audit_hash=audit_record.audit_hash,
            pool=checked_pool,
        )

    async def lease_proxy(
        self,
        *,
        tenant_id: str,
        pool_id: str,
        leased_by: str,
        correlation_id: str,
        selected_at: datetime | str | None = None,
        event_id: str | None = None,
    ) -> ProxyLease:
        pool = self.repository.require_pool(tenant_id=tenant_id, pool_id=pool_id)
        selected_at = normalize_datetime(selected_at or datetime.now(UTC))
        selected_index, proxy = select_live_proxy(pool)
        updated_pool = pool.model_copy(
            update={"rotation_cursor": (selected_index + 1) % len(pool.proxies)}
        )
        self.repository.save_pool(updated_pool)
        lease_id = _new_id("proxy-lease")
        audit_record = self.audit_logger.record(
            event_type=PROXY_LEASED_EVENT,
            tenant_id=tenant_id,
            metadata={
                "pool_id": pool.pool_id,
                "lease_id": lease_id,
                "proxy_id": proxy.proxy_id,
                "protocol": proxy.protocol.value,
                "url_hash": proxy.url_hash,
                "pool_revision": pool.revision,
                "rotation_strategy": pool.rotation_strategy.value,
            },
            timestamp=selected_at,
            correlation_id=correlation_id,
            actor_hash=subject_ref_hash(tenant_id=tenant_id, subject_id=leased_by),
            source=PROXY_ROTATION_SOURCE,
        )
        lease = ProxyLease(
            tenant_id=tenant_id,
            pool_id=pool.pool_id,
            lease_id=lease_id,
            proxy_id=proxy.proxy_id,
            protocol=proxy.protocol,
            redacted_url=proxy.redacted_url,
            url_hash=proxy.url_hash,
            secret_ref_hash=proxy.secret_ref_hash,
            health_status=proxy.health_status,
            pool_revision=pool.revision,
            rotation_strategy=pool.rotation_strategy,
            selected_at=selected_at,
            audit_hash=audit_record.audit_hash,
        )
        await self.publisher.publish(
            EventEnvelope(
                event_id=event_id or _new_id("evt-proxy-leased"),
                type=PROXY_LEASED_EVENT,
                schema_version=PROXY_ROTATION_SCHEMA_VERSION,
                tenant_id=tenant_id,
                source=PROXY_ROTATION_SOURCE,
                correlation_id=correlation_id,
                occurred_at=selected_at,
                payload={
                    "pool_id": pool.pool_id,
                    "lease_id": lease.lease_id,
                    "proxy_id": proxy.proxy_id,
                    "protocol": proxy.protocol.value,
                    "url_hash": proxy.url_hash,
                    "pool_revision": pool.revision,
                    "audit_hash": lease.audit_hash,
                },
            )
        )
        return lease


def build_proxy_pool_state(
    *,
    tenant_id: str,
    pool_id: str,
    platform: str,
    rotation_strategy: ProxyRotationStrategy,
    revision: int,
    rotation_cursor: int,
    proxies: tuple[ProxyEndpointState, ...],
    updated_by: str,
    updated_at: datetime,
    metadata: Mapping[str, JSONValue],
    audit_hash: str = DEFAULT_PROXY_AUDIT_HASH,
) -> ProxyPoolState:
    if len(proxies) == 0:
        raise ProxyRotationError("Прокси-пул должен содержать хотя бы один proxy")

    ordered_proxies = tuple(
        sorted(proxies, key=lambda item: (item.priority, item.proxy_id))
    )
    return ProxyPoolState(
        tenant_id=tenant_id,
        pool_id=pool_id,
        platform=platform.strip().lower(),
        rotation_strategy=rotation_strategy,
        revision=revision,
        rotation_cursor=rotation_cursor % len(ordered_proxies),
        total_proxy_count=len(ordered_proxies),
        healthy_proxy_count=sum(1 for proxy in ordered_proxies if proxy.is_leasable()),
        unhealthy_proxy_count=sum(
            1
            for proxy in ordered_proxies
            if proxy.health_status is ProxyHealthStatus.UNHEALTHY
        ),
        disabled_proxy_count=sum(
            1
            for proxy in ordered_proxies
            if proxy.health_status is ProxyHealthStatus.DISABLED
        ),
        proxies=ordered_proxies,
        updated_by=updated_by,
        updated_at=updated_at,
        audit_hash=audit_hash,
        metadata=dict(metadata),
    )


def apply_proxy_health_checks(
    *,
    pool: ProxyPoolState,
    checks: tuple[ProxyHealthSignal, ...],
    updated_by: str,
    updated_at: datetime,
) -> ProxyPoolState:
    by_proxy_id = {proxy.proxy_id: proxy for proxy in pool.proxies}
    for check in checks:
        if check.proxy_id not in by_proxy_id:
            raise ProxyRotationError(
                f"Прокси {check.proxy_id} не найден в пуле {pool.pool_id}"
            )

        current = by_proxy_id[check.proxy_id]
        checked_at = check.checked_at or updated_at
        if check.alive:
            by_proxy_id[check.proxy_id] = current.model_copy(
                update={
                    "health_status": (
                        ProxyHealthStatus.HEALTHY
                        if current.enabled
                        else ProxyHealthStatus.DISABLED
                    ),
                    "consecutive_failures": 0,
                    "success_count": current.success_count + 1,
                    "last_checked_at": checked_at,
                    "last_latency_ms": check.latency_ms,
                    "last_failure_reason": None,
                }
            )
        else:
            by_proxy_id[check.proxy_id] = current.model_copy(
                update={
                    "health_status": ProxyHealthStatus.UNHEALTHY,
                    "consecutive_failures": current.consecutive_failures + 1,
                    "last_checked_at": checked_at,
                    "last_latency_ms": check.latency_ms,
                    "last_failure_reason": check.reason_code or "health_check_failed",
                }
            )

    return build_proxy_pool_state(
        tenant_id=pool.tenant_id,
        pool_id=pool.pool_id,
        platform=pool.platform,
        rotation_strategy=pool.rotation_strategy,
        revision=pool.revision + 1,
        rotation_cursor=pool.rotation_cursor,
        proxies=tuple(by_proxy_id.values()),
        updated_by=updated_by,
        updated_at=updated_at,
        metadata=pool.metadata,
        audit_hash=pool.audit_hash,
    )


def select_live_proxy(pool: ProxyPoolState) -> tuple[int, ProxyEndpointState]:
    for offset in range(len(pool.proxies)):
        index = (pool.rotation_cursor + offset) % len(pool.proxies)
        proxy = pool.proxies[index]
        if proxy.is_leasable():
            return index, proxy

    raise ProxyUnavailableError("В прокси-пуле нет живых proxy")


def normalize_datetime(value: datetime | str) -> datetime:
    if isinstance(value, str):
        normalized = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        normalized = value

    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=UTC)

    return normalized.astimezone(UTC)


def subject_ref_hash(*, tenant_id: str, subject_id: str) -> str:
    payload = f"{tenant_id}:{subject_id}".encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _state_from_config(config: ProxyEndpointConfig) -> ProxyEndpointState:
    return ProxyEndpointState(
        proxy_id=config.proxy_id,
        protocol=config.protocol,
        redacted_url=_redacted_proxy_url(config.url),
        url_hash=_scoped_proxy_hash(config.url),
        secret_ref_hash=(
            _scoped_proxy_hash(config.secret_ref)
            if config.secret_ref is not None
            else None
        ),
        priority=config.priority,
        enabled=config.enabled,
        health_status=(
            ProxyHealthStatus.HEALTHY if config.enabled else ProxyHealthStatus.DISABLED
        ),
        metadata=config.metadata,
    )


def _ensure_unique_proxy_ids(proxies: tuple[ProxyEndpointState, ...]) -> None:
    proxy_ids = [proxy.proxy_id for proxy in proxies]
    if len(proxy_ids) != len(set(proxy_ids)):
        raise ProxyRotationError("proxy_id должен быть уникальным внутри пула")


def _redacted_proxy_url(value: str) -> str:
    parts = urlsplit(value)
    netloc = parts.netloc
    if parts.username is not None or parts.password is not None:
        hostname = parts.hostname or ""
        port = f":{parts.port}" if parts.port is not None else ""
        netloc = f"{hostname}{port}"

    return urlunsplit((parts.scheme.lower(), netloc, parts.path, parts.query, ""))


def _scoped_proxy_hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pool_audit_metadata(pool: ProxyPoolState) -> dict[str, JSONValue]:
    return {
        "pool_id": pool.pool_id,
        "platform": pool.platform,
        "revision": pool.revision,
        "rotation_strategy": pool.rotation_strategy.value,
        "total_proxy_count": pool.total_proxy_count,
        "healthy_proxy_count": pool.healthy_proxy_count,
        "unhealthy_proxy_count": pool.unhealthy_proxy_count,
        "disabled_proxy_count": pool.disabled_proxy_count,
        "proxy_ids": _json_string_list(proxy.proxy_id for proxy in pool.proxies),
        "protocols": _json_string_list(
            sorted({proxy.protocol.value for proxy in pool.proxies})
        ),
        "metadata_keys": _json_string_list(sorted(pool.metadata)),
    }


def _json_string_list(values: Iterable[str]) -> list[JSONValue]:
    return [value for value in values]


def _new_id(prefix: str) -> IdempotencyKey:
    return f"{prefix}-{uuid4()}"
