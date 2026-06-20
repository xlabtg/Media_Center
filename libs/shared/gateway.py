from __future__ import annotations

import json
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from libs.shared.tenant import TenantContext, TenantCoreError, require_tenant_context
from libs.shared.tenant_resources import (
    TenantResourceLimitError,
    TenantResourceManager,
)

RATE_LIMITED_CODE = "rate_limited"
SERVICE_NOT_FOUND_CODE = "service_not_found"

ASGIMessage = dict[str, object]
ASGIScope = dict[str, object]
Receive = Callable[[], Awaitable[ASGIMessage]]
Send = Callable[[ASGIMessage], Awaitable[None]]
ASGIApp = Callable[[ASGIScope, Receive, Send], Awaitable[None]]
Clock = Callable[[], float]

_HOP_BY_HOP_HEADERS = frozenset(
    {
        b"connection",
        b"keep-alive",
        b"proxy-authenticate",
        b"proxy-authorization",
        b"te",
        b"trailer",
        b"transfer-encoding",
        b"upgrade",
    }
)
_GATEWAY_CONTEXT_HEADERS = frozenset(
    {
        b"x-actor-roles",
        b"x-correlation-id",
        b"x-forwarded-prefix",
        b"x-original-path",
        b"x-service-name",
        b"x-subject-id",
        b"x-tenant-id",
    }
)


@dataclass(frozen=True, slots=True)
class GatewayRoute:
    """Tenant-aware route from a public Gateway prefix to a service app."""

    service_name: str
    path_prefix: str
    downstream_app: ASGIApp

    def __post_init__(self) -> None:
        service_name = self.service_name.strip()
        path_prefix = _normalize_path_prefix(self.path_prefix)
        if service_name == "":
            raise ValueError("service_name должен быть непустой строкой")

        object.__setattr__(self, "service_name", service_name)
        object.__setattr__(self, "path_prefix", path_prefix)

    @classmethod
    def for_service(
        cls,
        service_name: str,
        downstream_app: ASGIApp,
    ) -> GatewayRoute:
        normalized_service = service_name.strip()
        return cls(
            service_name=normalized_service,
            path_prefix=f"/{normalized_service}",
            downstream_app=downstream_app,
        )

    def matches(self, path: str) -> bool:
        return path == self.path_prefix or path.startswith(f"{self.path_prefix}/")

    def downstream_path(self, path: str) -> str:
        suffix = path[len(self.path_prefix) :]
        return suffix if suffix != "" else "/"


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    """Fixed-window API Gateway rate limit."""

    limit: int
    window_seconds: int

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("limit должен быть положительным")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds должен быть положительным")


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_at: int
    retry_after_seconds: int


class RateLimiter(Protocol):
    def check(self, key: str) -> RateLimitDecision:
        """Return whether a request is allowed for the provided key."""


@dataclass(slots=True)
class _RateLimitWindow:
    started_at: int
    request_count: int


class InMemoryRateLimiter:
    """Deterministic fixed-window limiter for tests and local Gateway wiring."""

    def __init__(
        self,
        policy: RateLimitPolicy,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._policy = policy
        self._clock = clock or time.time
        self._windows: dict[str, _RateLimitWindow] = {}

    @property
    def policy(self) -> RateLimitPolicy:
        return self._policy

    def check(self, key: str) -> RateLimitDecision:
        if key.strip() == "":
            raise ValueError("rate limit key должен быть непустой строкой")

        now = int(self._clock())
        window_started_at = now - (now % self._policy.window_seconds)
        reset_at = window_started_at + self._policy.window_seconds
        window = self._windows.get(key)
        if window is None or window.started_at != window_started_at:
            window = _RateLimitWindow(
                started_at=window_started_at,
                request_count=0,
            )
            self._windows[key] = window

        if window.request_count >= self._policy.limit:
            return RateLimitDecision(
                allowed=False,
                limit=self._policy.limit,
                remaining=0,
                reset_at=reset_at,
                retry_after_seconds=max(1, reset_at - now),
            )

        window.request_count += 1
        return RateLimitDecision(
            allowed=True,
            limit=self._policy.limit,
            remaining=self._policy.limit - window.request_count,
            reset_at=reset_at,
            retry_after_seconds=0,
        )


class RateLimitedError(TenantCoreError):
    def __init__(
        self,
        *,
        decision: RateLimitDecision,
        policy: RateLimitPolicy,
        correlation_id: str | None,
    ) -> None:
        self.retry_after_seconds = decision.retry_after_seconds
        self.limit = decision.limit
        self.remaining = decision.remaining
        self.reset_at = decision.reset_at
        super().__init__(
            status_code=429,
            error_code=RATE_LIMITED_CODE,
            message="Превышен лимит запросов API Gateway",
            details={
                "limit": policy.limit,
                "window_seconds": policy.window_seconds,
                "retry_after_seconds": decision.retry_after_seconds,
            },
            correlation_id=correlation_id,
        )


class GatewayRouteNotFoundError(TenantCoreError):
    def __init__(
        self,
        path: str,
        *,
        correlation_id: str | None,
    ) -> None:
        super().__init__(
            status_code=404,
            error_code=SERVICE_NOT_FOUND_CODE,
            message="Маршрут API Gateway не найден",
            details={"path": path},
            correlation_id=correlation_id,
        )


class APIGatewayASGIMiddleware:
    """ASGI proxy layer for tenant-aware service routing and rate limits."""

    def __init__(
        self,
        *,
        routes: Iterable[GatewayRoute],
        rate_limiter: RateLimiter,
        resource_manager: TenantResourceManager | None = None,
    ) -> None:
        normalized_routes = tuple(
            sorted(routes, key=lambda route: len(route.path_prefix), reverse=True)
        )
        if len(normalized_routes) == 0:
            raise ValueError("routes должен содержать хотя бы один маршрут")

        self._routes = normalized_routes
        self._rate_limiter = rate_limiter
        self._resource_manager = resource_manager

    async def __call__(
        self,
        scope: ASGIScope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope.get("type") != "http":
            await _send_error(
                send,
                GatewayRouteNotFoundError(
                    _scope_string(scope, "path"),
                    correlation_id=None,
                ),
            )
            return

        context: TenantContext | None = None
        operation_name: str | None = None
        operation_slot_acquired = False

        try:
            context = require_tenant_context()
            route = self._route_for_scope(scope, context)
            operation_name = _operation_name(scope, route)
            decision = self._rate_limiter.check(
                _rate_limit_key(
                    context=context,
                    route=route,
                )
            )
            if not decision.allowed:
                raise RateLimitedError(
                    decision=decision,
                    policy=_rate_limit_policy(self._rate_limiter),
                    correlation_id=context.correlation_id,
                )

            if self._resource_manager is not None:
                request_decision = self._resource_manager.admit_request(
                    context,
                    service_name=route.service_name,
                    operation=operation_name,
                )
                if not request_decision.allowed:
                    raise TenantResourceLimitError(
                        request_decision,
                        correlation_id=context.correlation_id,
                    )

                operation_decision = self._resource_manager.acquire_operation_slot(
                    context,
                    operation=operation_name,
                )
                if not operation_decision.allowed:
                    raise TenantResourceLimitError(
                        operation_decision,
                        correlation_id=context.correlation_id,
                    )
                operation_slot_acquired = True

            downstream_scope = _downstream_scope(
                scope,
                route=route,
                context=context,
            )
        except TenantCoreError as error:
            await _send_error(send, error)
            return

        try:
            await route.downstream_app(downstream_scope, receive, send)
        finally:
            if (
                operation_slot_acquired
                and self._resource_manager is not None
                and context is not None
                and operation_name is not None
            ):
                self._resource_manager.release_operation_slot(
                    context,
                    operation=operation_name,
                )

    def _route_for_scope(
        self,
        scope: ASGIScope,
        context: TenantContext,
    ) -> GatewayRoute:
        path = _scope_string(scope, "path")
        for route in self._routes:
            if route.matches(path):
                return route

        raise GatewayRouteNotFoundError(path, correlation_id=context.correlation_id)


def _rate_limit_key(
    *,
    context: TenantContext,
    route: GatewayRoute,
) -> str:
    subject = context.subject or "anonymous"
    return f"tenant:{context.tenant_id}:subject:{subject}:service:{route.service_name}"


def _operation_name(scope: ASGIScope, route: GatewayRoute) -> str:
    method = _scope_string(scope, "method").lower() or "http"
    return f"{method}.{route.service_name}"


def _rate_limit_policy(rate_limiter: RateLimiter) -> RateLimitPolicy:
    policy = getattr(rate_limiter, "policy", None)
    if isinstance(policy, RateLimitPolicy):
        return policy

    return RateLimitPolicy(limit=1, window_seconds=1)


def _downstream_scope(
    scope: ASGIScope,
    *,
    route: GatewayRoute,
    context: TenantContext,
) -> ASGIScope:
    original_path = _scope_string(scope, "path")
    downstream_path = route.downstream_path(original_path)
    downstream_scope = dict(scope)
    downstream_scope["path"] = downstream_path
    downstream_scope["root_path"] = _root_path(scope, route)
    downstream_scope["headers"] = _downstream_headers(
        scope,
        route=route,
        context=context,
        original_path=original_path,
    )
    if "raw_path" in downstream_scope:
        downstream_scope["raw_path"] = downstream_path.encode("utf-8")

    return downstream_scope


def _root_path(scope: ASGIScope, route: GatewayRoute) -> str:
    current_root = _scope_string(scope, "root_path").rstrip("/")
    return f"{current_root}{route.path_prefix}"


def _downstream_headers(
    scope: ASGIScope,
    *,
    route: GatewayRoute,
    context: TenantContext,
    original_path: str,
) -> list[tuple[bytes, bytes]]:
    raw_headers = _raw_headers_from_scope(scope)
    correlation_id = context.correlation_id or _header_value(
        raw_headers,
        b"x-correlation-id",
    )
    if correlation_id is None:
        correlation_id = uuid.uuid4().hex

    headers = [
        (name, value)
        for name, value in raw_headers
        if name.lower() not in _HOP_BY_HOP_HEADERS
        and name.lower() not in _GATEWAY_CONTEXT_HEADERS
    ]
    headers.extend(
        [
            (b"x-correlation-id", _header_bytes(correlation_id)),
            (b"x-tenant-id", _header_bytes(context.tenant_id)),
            (b"x-service-name", b"api-gateway"),
            (b"x-forwarded-prefix", _header_bytes(route.path_prefix)),
            (b"x-original-path", _header_bytes(original_path)),
            (b"x-actor-roles", _header_bytes(",".join(context.roles))),
        ]
    )
    if context.subject is not None:
        headers.append((b"x-subject-id", _header_bytes(context.subject)))

    return headers


def _raw_headers_from_scope(scope: ASGIScope) -> tuple[tuple[bytes, bytes], ...]:
    raw_headers = scope.get("headers", ())
    if not isinstance(raw_headers, list | tuple):
        return ()

    headers: list[tuple[bytes, bytes]] = []
    for raw_header in raw_headers:
        if (
            isinstance(raw_header, tuple)
            and len(raw_header) == 2
            and isinstance(raw_header[0], bytes)
            and isinstance(raw_header[1], bytes)
        ):
            headers.append((raw_header[0], raw_header[1]))

    return tuple(headers)


def _header_value(
    headers: tuple[tuple[bytes, bytes], ...],
    name: bytes,
) -> str | None:
    normalized_name = name.lower()
    for raw_name, raw_value in headers:
        if raw_name.lower() == normalized_name:
            return raw_value.decode("latin-1")

    return None


def _header_bytes(value: str) -> bytes:
    return value.encode("utf-8")


async def _send_error(send: Send, error: TenantCoreError) -> None:
    body = json.dumps(error.to_response_body(), ensure_ascii=False).encode("utf-8")
    headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    if isinstance(error, RateLimitedError):
        headers.extend(
            [
                (b"retry-after", str(error.retry_after_seconds).encode("ascii")),
                (b"x-ratelimit-limit", str(error.limit).encode("ascii")),
                (b"x-ratelimit-remaining", str(error.remaining).encode("ascii")),
                (b"x-ratelimit-reset", str(error.reset_at).encode("ascii")),
            ]
        )

    await send(
        {
            "type": "http.response.start",
            "status": error.status_code,
            "headers": headers,
        }
    )
    await send({"type": "http.response.body", "body": body})


def _normalize_path_prefix(path_prefix: str) -> str:
    normalized = path_prefix.strip().rstrip("/")
    if normalized == "":
        raise ValueError("path_prefix должен быть непустой строкой")
    if not normalized.startswith("/"):
        raise ValueError("path_prefix должен начинаться с /")
    if normalized == "/":
        raise ValueError("path_prefix должен указывать service prefix, а не /")

    return normalized


def _scope_string(scope: ASGIScope, key: str) -> str:
    value = scope.get(key)
    if isinstance(value, str):
        return value

    return ""
