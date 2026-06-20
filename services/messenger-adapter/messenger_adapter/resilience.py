from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from pydantic import ConfigDict, Field, field_validator, model_validator

from libs.shared.models import (
    IdempotencyKey,
    JSONValue,
    SharedBaseModel,
    TenantId,
)
from messenger_adapter.base_adapter import (
    PlatformPublicationError,
    PlatformPublishCommand,
    PlatformPublisher,
    PlatformPublishResult,
)

_PLATFORM_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"
_CHANNEL_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_SECRET_REF_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"
_SHA256_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"
_HTTP_SCHEMES = frozenset({"http", "https"})
_SOCKS5_SCHEMES = frozenset({"socks5", "socks5h"})
_MTPROTO_SCHEMES = frozenset({"mtproto"})


class IntegrationResilienceError(RuntimeError):
    """Base error for anti-blocking/fallback integration contracts."""


class ProxyLeaseUnavailableError(IntegrationResilienceError):
    """Raised when a required proxy lease cannot be issued."""


class FallbackChannelUnavailableError(IntegrationResilienceError):
    """Raised when no legal fallback channel can accept the publication."""


class IntegrationProxyProtocol(StrEnum):
    HTTP = "http"
    SOCKS5 = "socks5"
    MTPROTO = "mtproto"


class IntegrationProxyHealth(StrEnum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DISABLED = "disabled"


class FallbackChannelType(StrEnum):
    IPFS = "ipfs"
    TON = "ton"
    MATRIX = "matrix"


class FallbackChannelStatus(StrEnum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DISABLED = "disabled"


class IntegrationProxyEndpoint(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    proxy_id: IdempotencyKey
    protocol: IntegrationProxyProtocol
    url: str = Field(min_length=1, max_length=2048)
    secret_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=_SECRET_REF_PATTERN,
    )
    priority: int = Field(default=100, ge=0, le=10_000)
    enabled: bool = True
    health_status: IntegrationProxyHealth = IntegrationProxyHealth.HEALTHY
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
                "",
            )
        )

    @model_validator(mode="after")
    def _validate_protocol_scheme(self) -> IntegrationProxyEndpoint:
        scheme = urlsplit(self.url).scheme.lower()
        if (
            self.protocol is IntegrationProxyProtocol.HTTP
            and scheme not in _HTTP_SCHEMES
        ):
            raise ValueError("HTTP proxy должен использовать схему http или https")
        if (
            self.protocol is IntegrationProxyProtocol.SOCKS5
            and scheme not in _SOCKS5_SCHEMES
        ):
            raise ValueError(
                "SOCKS5 proxy должен использовать схему socks5 или socks5h"
            )
        if (
            self.protocol is IntegrationProxyProtocol.MTPROTO
            and scheme not in _MTPROTO_SCHEMES
        ):
            raise ValueError("MTProto proxy должен использовать схему mtproto")

        return self

    def is_leasable(self) -> bool:
        return self.enabled and self.health_status is IntegrationProxyHealth.HEALTHY


class IntegrationProxyLease(SharedBaseModel):
    tenant_id: TenantId
    platform: str = Field(pattern=_PLATFORM_PATTERN)
    lease_id: IdempotencyKey
    proxy_id: IdempotencyKey
    protocol: IntegrationProxyProtocol
    redacted_url_hash: str = Field(pattern=_SHA256_HASH_PATTERN)
    secret_ref_hash: str | None = Field(default=None, pattern=_SHA256_HASH_PATTERN)
    selected_at: datetime

    @field_validator("platform", mode="before")
    @classmethod
    def _normalize_platform(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("selected_at")
    @classmethod
    def _normalize_selected_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class ProxyLeaseProvider(Protocol):
    async def lease_proxy(
        self,
        *,
        tenant_id: str,
        platform: str,
        correlation_id: str,
    ) -> IntegrationProxyLease:
        """Issue a tenant/platform scoped proxy lease for an integration call."""


class FallbackChannelRoute(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    tenant_id: TenantId
    platform: str = Field(pattern=_PLATFORM_PATTERN)
    channel_type: FallbackChannelType
    channel_id: str = Field(min_length=1, max_length=128, pattern=_CHANNEL_ID_PATTERN)
    priority: int = Field(default=100, ge=0, le=10_000)
    endpoint: str = Field(min_length=1, max_length=2048)
    secret_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=_SECRET_REF_PATTERN,
    )
    status: FallbackChannelStatus = FallbackChannelStatus.HEALTHY
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("platform", mode="before")
    @classmethod
    def _normalize_platform(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("endpoint")
    @classmethod
    def _validate_endpoint(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme == "" or parts.netloc == "":
            raise ValueError("fallback endpoint должен быть абсолютным URI")
        if parts.username is not None or parts.password is not None:
            raise ValueError(
                "fallback credentials нельзя хранить в endpoint; используйте secret_ref"
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

    @model_validator(mode="after")
    def _validate_endpoint_scheme(self) -> FallbackChannelRoute:
        scheme = urlsplit(self.endpoint).scheme.lower()
        if scheme != self.channel_type.value:
            raise ValueError("scheme fallback endpoint должен совпадать с channel_type")
        return self

    def is_available(self) -> bool:
        return self.status is FallbackChannelStatus.HEALTHY


class FallbackChannelSnapshot(SharedBaseModel):
    tenant_id: TenantId
    platform: str = Field(pattern=_PLATFORM_PATTERN)
    total_channel_count: int = Field(ge=0)
    healthy_channel_count: int = Field(ge=0)
    unhealthy_channel_count: int = Field(ge=0)
    disabled_channel_count: int = Field(ge=0)
    channel_statuses: dict[str, FallbackChannelStatus] = Field(default_factory=dict)


class FallbackPublicationResult(PlatformPublishResult):
    tenant_id: TenantId
    channel_type: FallbackChannelType
    channel_id: str = Field(min_length=1, max_length=128, pattern=_CHANNEL_ID_PATTERN)
    gateway_ref_hash: str = Field(pattern=_SHA256_HASH_PATTERN)
    content_hash: str = Field(pattern=_SHA256_HASH_PATTERN)
    endpoint_hash: str = Field(pattern=_SHA256_HASH_PATTERN)
    secret_ref_hash: str | None = Field(default=None, pattern=_SHA256_HASH_PATTERN)
    fallback_reason_code: str = Field(min_length=1, max_length=128)

    @classmethod
    def from_platform_result(
        cls,
        *,
        tenant_id: str,
        command: PlatformPublishCommand,
        route: FallbackChannelRoute,
        result: PlatformPublishResult,
        fallback_reason_code: str,
    ) -> FallbackPublicationResult:
        if isinstance(result, FallbackPublicationResult):
            return result

        return cls(
            tenant_id=tenant_id,
            platform=result.platform,
            platform_ref=result.platform_ref,
            connector_name=result.connector_name,
            published_at=result.published_at,
            channel_type=route.channel_type,
            channel_id=route.channel_id,
            gateway_ref_hash=_scoped_hash(
                tenant_id=tenant_id,
                value=(
                    f"{route.channel_type.value}:{route.channel_id}:"
                    f"{result.platform_ref}"
                ),
            ),
            content_hash=_scoped_hash(
                tenant_id=tenant_id,
                value=f"{command.platform}:{command.publication_id}:{command.content}",
            ),
            endpoint_hash=_scoped_hash(tenant_id=tenant_id, value=route.endpoint),
            secret_ref_hash=(
                _scoped_hash(tenant_id=tenant_id, value=route.secret_ref)
                if route.secret_ref is not None
                else None
            ),
            fallback_reason_code=fallback_reason_code,
        )


class FallbackChannelRegistry(Protocol):
    def routes_for(
        self,
        *,
        tenant_id: str,
        platform: str,
        channel_types: tuple[FallbackChannelType, ...] | None = None,
    ) -> tuple[FallbackChannelRoute, ...]:
        """Return available fallback routes in deterministic priority order."""

    def mark_unhealthy(
        self,
        *,
        tenant_id: str,
        platform: str,
        channel_id: str,
        reason_code: str,
    ) -> None:
        """Mark a fallback route as temporarily unavailable."""

    def mark_healthy(
        self,
        *,
        tenant_id: str,
        platform: str,
        channel_id: str,
    ) -> None:
        """Mark a fallback route as healthy after a successful delivery."""

    def snapshot(self, *, tenant_id: str, platform: str) -> FallbackChannelSnapshot:
        """Return public health counters without raw endpoints or secrets."""


class FallbackChannelPublisher(Protocol):
    async def publish(
        self,
        command: PlatformPublishCommand,
        route: FallbackChannelRoute,
    ) -> PlatformPublishResult:
        """Publish through a legal fallback channel such as IPFS, TON or Matrix."""


class ResiliencePolicy(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    proxy_pool_required: bool = False
    fallback_channel_types: tuple[FallbackChannelType, ...] = (
        FallbackChannelType.IPFS,
        FallbackChannelType.TON,
        FallbackChannelType.MATRIX,
    )
    fallback_on_error_codes: tuple[str, ...] = (
        "platform_timeout",
        "platform_unavailable",
        "rate_limited",
        "publication_failed",
    )
    max_fallback_attempts: int = Field(default=3, ge=1, le=16)

    @field_validator("fallback_channel_types", "fallback_on_error_codes", mode="before")
    @classmethod
    def _normalize_tuple(cls, value: object) -> object:
        if isinstance(value, str):
            return (value.strip(),)
        if isinstance(value, list | tuple):
            return tuple(value)
        return value

    def should_fallback(self, error: PlatformPublicationError) -> bool:
        if not error.retryable:
            return False

        normalized = error.error_code.strip().lower()
        return normalized in {
            error_code.strip().lower() for error_code in self.fallback_on_error_codes
        }


@dataclass(slots=True)
class InMemoryProxyLeaseProvider:
    _pools: dict[tuple[str, str], tuple[IntegrationProxyEndpoint, ...]] = field(
        default_factory=dict,
        init=False,
    )
    _cursors: dict[tuple[str, str], int] = field(default_factory=dict, init=False)
    _leases: list[IntegrationProxyLease] = field(default_factory=list, init=False)

    @property
    def leases(self) -> tuple[IntegrationProxyLease, ...]:
        return tuple(self._leases)

    @property
    def leased_proxy_ids(self) -> tuple[str, ...]:
        return tuple(lease.proxy_id for lease in self._leases)

    def register_pool(
        self,
        *,
        tenant_id: str,
        platform: str,
        proxies: Sequence[IntegrationProxyEndpoint | Mapping[str, object]],
    ) -> None:
        if len(proxies) == 0:
            raise ValueError("proxy pool должен содержать хотя бы один endpoint")

        normalized_platform = _normalize_platform(platform)
        endpoints = tuple(
            (
                proxy
                if isinstance(proxy, IntegrationProxyEndpoint)
                else IntegrationProxyEndpoint.model_validate(proxy)
            )
            for proxy in proxies
        )
        proxy_ids = [endpoint.proxy_id for endpoint in endpoints]
        if len(proxy_ids) != len(set(proxy_ids)):
            raise ValueError("proxy_id должен быть уникальным внутри pool")

        key = (tenant_id, normalized_platform)
        self._pools[key] = tuple(
            sorted(endpoints, key=lambda item: (item.priority, item.proxy_id))
        )
        self._cursors[key] = 0

    async def lease_proxy(
        self,
        *,
        tenant_id: str,
        platform: str,
        correlation_id: str,
    ) -> IntegrationProxyLease:
        normalized_platform = _normalize_platform(platform)
        key = (tenant_id, normalized_platform)
        endpoints = self._pools.get(key)
        if endpoints is None:
            raise ProxyLeaseUnavailableError("proxy pool не найден для tenant/platform")

        cursor = self._cursors.get(key, 0)
        for offset in range(len(endpoints)):
            index = (cursor + offset) % len(endpoints)
            endpoint = endpoints[index]
            if not endpoint.is_leasable():
                continue

            self._cursors[key] = (index + 1) % len(endpoints)
            lease = IntegrationProxyLease(
                tenant_id=tenant_id,
                platform=normalized_platform,
                lease_id=_new_id("integration-proxy-lease"),
                proxy_id=endpoint.proxy_id,
                protocol=endpoint.protocol,
                redacted_url_hash=_scoped_hash(
                    tenant_id=tenant_id,
                    value=_redacted_url(endpoint.url),
                ),
                secret_ref_hash=(
                    _scoped_hash(tenant_id=tenant_id, value=endpoint.secret_ref)
                    if endpoint.secret_ref is not None
                    else None
                ),
                selected_at=datetime.now(UTC),
            )
            self._leases.append(lease)
            return lease

        raise ProxyLeaseUnavailableError("в proxy pool нет живых endpoint")


@dataclass(slots=True)
class InMemoryFallbackChannelRegistry:
    routes: Sequence[FallbackChannelRoute | Mapping[str, object]] = field(
        default_factory=tuple
    )
    _routes: dict[tuple[str, str, str], FallbackChannelRoute] = field(
        default_factory=dict,
        init=False,
    )

    def __post_init__(self) -> None:
        for route in self.routes:
            normalized = (
                route
                if isinstance(route, FallbackChannelRoute)
                else FallbackChannelRoute.model_validate(route)
            )
            self._routes[_route_key(normalized)] = normalized

    def routes_for(
        self,
        *,
        tenant_id: str,
        platform: str,
        channel_types: tuple[FallbackChannelType, ...] | None = None,
    ) -> tuple[FallbackChannelRoute, ...]:
        normalized_platform = _normalize_platform(platform)
        allowed_types = set(channel_types) if channel_types is not None else None
        routes = [
            route
            for route in self._routes.values()
            if route.tenant_id == tenant_id
            and route.platform == normalized_platform
            and route.is_available()
            and (allowed_types is None or route.channel_type in allowed_types)
        ]
        return tuple(sorted(routes, key=lambda item: (item.priority, item.channel_id)))

    def mark_unhealthy(
        self,
        *,
        tenant_id: str,
        platform: str,
        channel_id: str,
        reason_code: str,
    ) -> None:
        self._update_status(
            tenant_id=tenant_id,
            platform=platform,
            channel_id=channel_id,
            status=FallbackChannelStatus.UNHEALTHY,
        )

    def mark_healthy(
        self,
        *,
        tenant_id: str,
        platform: str,
        channel_id: str,
    ) -> None:
        self._update_status(
            tenant_id=tenant_id,
            platform=platform,
            channel_id=channel_id,
            status=FallbackChannelStatus.HEALTHY,
        )

    def snapshot(self, *, tenant_id: str, platform: str) -> FallbackChannelSnapshot:
        normalized_platform = _normalize_platform(platform)
        routes = [
            route
            for route in self._routes.values()
            if route.tenant_id == tenant_id and route.platform == normalized_platform
        ]
        return FallbackChannelSnapshot(
            tenant_id=tenant_id,
            platform=normalized_platform,
            total_channel_count=len(routes),
            healthy_channel_count=sum(
                1 for route in routes if route.status is FallbackChannelStatus.HEALTHY
            ),
            unhealthy_channel_count=sum(
                1 for route in routes if route.status is FallbackChannelStatus.UNHEALTHY
            ),
            disabled_channel_count=sum(
                1 for route in routes if route.status is FallbackChannelStatus.DISABLED
            ),
            channel_statuses={
                route.channel_id: route.status
                for route in sorted(routes, key=lambda item: item.channel_id)
            },
        )

    def _update_status(
        self,
        *,
        tenant_id: str,
        platform: str,
        channel_id: str,
        status: FallbackChannelStatus,
    ) -> None:
        normalized_platform = _normalize_platform(platform)
        key = (tenant_id, normalized_platform, channel_id)
        route = self._routes.get(key)
        if route is None:
            raise FallbackChannelUnavailableError(
                "fallback channel не найден для tenant/platform"
            )

        self._routes[key] = route.model_copy(update={"status": status})


@dataclass(slots=True)
class ResilientPlatformPublisher:
    primary: PlatformPublisher
    proxy_leases: ProxyLeaseProvider | None = None
    fallback_routes: FallbackChannelRegistry | None = None
    fallback_publisher: FallbackChannelPublisher | None = None
    policy: ResiliencePolicy = field(default_factory=ResiliencePolicy)
    _proxy_leases: list[IntegrationProxyLease] = field(default_factory=list, init=False)
    _fallback_results: list[FallbackPublicationResult] = field(
        default_factory=list,
        init=False,
    )

    @property
    def issued_proxy_leases(self) -> tuple[IntegrationProxyLease, ...]:
        return tuple(self._proxy_leases)

    @property
    def fallback_results(self) -> tuple[FallbackPublicationResult, ...]:
        return tuple(self._fallback_results)

    async def publish(
        self,
        command: PlatformPublishCommand,
    ) -> PlatformPublishResult:
        proxy_lease = await self._lease_proxy(command)
        command_for_primary = _command_with_proxy_metadata(command, proxy_lease)

        try:
            return await self.primary.publish(command_for_primary)
        except Exception as error:
            publication_error = _as_publication_error(
                error,
                platform=command.platform,
            )
            if not self.policy.should_fallback(publication_error):
                raise publication_error from error

            return await self._publish_to_fallback(
                command=command_for_primary,
                publication_error=publication_error,
            )

    async def _lease_proxy(
        self,
        command: PlatformPublishCommand,
    ) -> IntegrationProxyLease | None:
        if self.proxy_leases is None:
            if self.policy.proxy_pool_required:
                raise PlatformPublicationError(
                    "Для интеграции требуется proxy pool",
                    platform=command.platform,
                    error_code="proxy_pool_missing",
                    retryable=True,
                )
            return None

        try:
            lease = await self.proxy_leases.lease_proxy(
                tenant_id=command.tenant_id,
                platform=command.platform,
                correlation_id=command.correlation_id,
            )
        except ProxyLeaseUnavailableError as error:
            if self.policy.proxy_pool_required:
                raise PlatformPublicationError(
                    "Proxy lease недоступен для интеграции",
                    platform=command.platform,
                    error_code="proxy_unavailable",
                    retryable=True,
                ) from error
            return None

        self._proxy_leases.append(lease)
        return lease

    async def _publish_to_fallback(
        self,
        *,
        command: PlatformPublishCommand,
        publication_error: PlatformPublicationError,
    ) -> FallbackPublicationResult:
        if self.fallback_routes is None or self.fallback_publisher is None:
            raise publication_error

        routes = self.fallback_routes.routes_for(
            tenant_id=command.tenant_id,
            platform=command.platform,
            channel_types=self.policy.fallback_channel_types,
        )[: self.policy.max_fallback_attempts]
        if len(routes) == 0:
            raise publication_error

        last_error: PlatformPublicationError = publication_error
        for route in routes:
            try:
                fallback_result = await self.fallback_publisher.publish(
                    command,
                    route,
                )
            except Exception as error:
                last_error = _as_publication_error(error, platform=command.platform)
                self.fallback_routes.mark_unhealthy(
                    tenant_id=command.tenant_id,
                    platform=command.platform,
                    channel_id=route.channel_id,
                    reason_code=last_error.error_code,
                )
                continue

            normalized = FallbackPublicationResult.from_platform_result(
                tenant_id=command.tenant_id,
                command=command,
                route=route,
                result=fallback_result,
                fallback_reason_code=publication_error.error_code,
            )
            self.fallback_routes.mark_healthy(
                tenant_id=command.tenant_id,
                platform=command.platform,
                channel_id=route.channel_id,
            )
            self._fallback_results.append(normalized)
            return normalized

        raise PlatformPublicationError(
            "Все fallback channels недоступны",
            platform=command.platform,
            error_code="fallback_channels_unavailable",
            retryable=last_error.retryable,
        ) from last_error


def _command_with_proxy_metadata(
    command: PlatformPublishCommand,
    proxy_lease: IntegrationProxyLease | None,
) -> PlatformPublishCommand:
    if proxy_lease is None:
        return command

    metadata = _copy_json_object(command.metadata)
    resilience_metadata = metadata.get("resilience")
    if not isinstance(resilience_metadata, dict):
        resilience_metadata = {}

    resilience_metadata["proxy"] = {
        "lease_id": proxy_lease.lease_id,
        "proxy_id": proxy_lease.proxy_id,
        "protocol": proxy_lease.protocol.value,
        "redacted_url_hash": proxy_lease.redacted_url_hash,
    }
    metadata["resilience"] = cast(JSONValue, resilience_metadata)
    data = command.model_dump(mode="python")
    data["metadata"] = metadata
    return PlatformPublishCommand(**data)


def _copy_json_object(value: Mapping[str, JSONValue]) -> dict[str, JSONValue]:
    result: dict[str, JSONValue] = {}
    for key, item in value.items():
        result[key] = _copy_json_value(item)
    return result


def _copy_json_value(value: JSONValue) -> JSONValue:
    if isinstance(value, dict):
        return {key: _copy_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_json_value(item) for item in value]
    return value


def _as_publication_error(
    error: Exception,
    *,
    platform: str,
) -> PlatformPublicationError:
    if isinstance(error, PlatformPublicationError):
        return error

    return PlatformPublicationError(
        "Сбой интеграции",
        platform=platform,
        error_code="publication_failed",
        retryable=True,
    )


def _route_key(route: FallbackChannelRoute) -> tuple[str, str, str]:
    return (route.tenant_id, route.platform, route.channel_id)


def _redacted_url(value: str) -> str:
    parts = urlsplit(value)
    return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path, parts.query, ""))


def _scoped_hash(*, tenant_id: str, value: str) -> str:
    return "sha256:" + hashlib.sha256(f"{tenant_id}:{value}".encode()).hexdigest()


def _normalize_platform(platform: str) -> str:
    normalized = platform.strip().lower()
    if normalized == "":
        raise ValueError("platform не может быть пустой")
    return normalized


def _new_id(prefix: str) -> IdempotencyKey:
    return f"{prefix}-{uuid4()}"
