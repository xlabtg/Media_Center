from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Self, cast

from fastapi import FastAPI, Response

from libs.shared.config import AppSettings
from libs.shared.db import DatabaseSettings
from libs.shared.models import RequestContextModel
from libs.shared.observability import (
    DEFAULT_METRICS_PATH,
    ObservabilityContext,
    TenantMetricRegistry,
)
from libs.shared.tenant import (
    AuditSink,
    TenantContextASGIMiddleware,
    require_tenant_context,
)

ASGIMessage = dict[str, object]
ASGIScope = dict[str, object]
Receive = Callable[[], Awaitable[ASGIMessage]]
Send = Callable[[ASGIMessage], Awaitable[None]]
ASGIApp = Callable[[ASGIScope, Receive, Send], Awaitable[None]]

DEFAULT_SERVICE_TEMPLATE_PUBLIC_PATHS = (
    "/health",
    DEFAULT_METRICS_PATH,
    "/docs",
    "/openapi.json",
    "/redoc",
)
PLATFORM_TENANT_ID = "platform"


@dataclass(frozen=True, slots=True)
class ServiceTemplateConfig:
    service_name: str
    jwt_secret: str | bytes
    version: str = "0.1.0"
    database_url: str | None = None
    prometheus_enabled: bool = True
    expected_issuer: str | None = None
    expected_audience: str | None = None
    public_paths: tuple[str, ...] = DEFAULT_SERVICE_TEMPLATE_PUBLIC_PATHS

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "service_name",
            _normalize_service_name(self.service_name),
        )
        object.__setattr__(self, "version", _normalize_version(self.version))
        object.__setattr__(self, "jwt_secret", _normalize_jwt_secret(self.jwt_secret))
        object.__setattr__(
            self,
            "database_url",
            _normalize_database_url(self.database_url),
        )
        object.__setattr__(
            self,
            "public_paths",
            _normalize_public_paths(self.public_paths),
        )

    @classmethod
    def from_app_settings(
        cls,
        settings: AppSettings,
        *,
        service_name: str,
        version: str = "0.1.0",
        public_paths: tuple[str, ...] = DEFAULT_SERVICE_TEMPLATE_PUBLIC_PATHS,
    ) -> Self:
        return cls(
            service_name=service_name,
            version=version,
            database_url=settings.database_url,
            jwt_secret=settings.jwt_secret.get_secret_value(),
            prometheus_enabled=settings.prometheus_enabled,
            public_paths=public_paths,
        )

    def to_database_settings(self) -> DatabaseSettings | None:
        if self.database_url is None:
            return None

        return DatabaseSettings(database_url=self.database_url)


@dataclass(frozen=True, slots=True)
class ServiceTemplateState:
    config: ServiceTemplateConfig
    metrics: TenantMetricRegistry
    database_settings: DatabaseSettings | None

    def database_status(self) -> str:
        if self.database_settings is None:
            return "not_configured"

        return "configured"

    def metrics_status(self) -> str:
        if self.config.prometheus_enabled:
            return "enabled"

        return "disabled"


class PublicPathTenantContextMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        jwt_secret: str | bytes,
        public_paths: Sequence[str] = DEFAULT_SERVICE_TEMPLATE_PUBLIC_PATHS,
        expected_issuer: str | None = None,
        expected_audience: str | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._app = app
        self._public_paths = _normalize_public_paths(tuple(public_paths))
        self._secured_app = TenantContextASGIMiddleware(
            app,
            jwt_secret=jwt_secret,
            expected_issuer=expected_issuer,
            expected_audience=expected_audience,
            audit_sink=audit_sink,
        )

    async def __call__(
        self,
        scope: ASGIScope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope.get("type") == "http":
            path = scope.get("path")
            if isinstance(path, str) and _is_public_path(path, self._public_paths):
                await self._app(scope, receive, send)
                return

        await self._secured_app(scope, receive, send)


def create_service_app(
    config: ServiceTemplateConfig,
    *,
    title: str | None = None,
    metrics: TenantMetricRegistry | None = None,
    audit_sink: AuditSink | None = None,
    docs_url: str | None = "/docs",
    redoc_url: str | None = "/redoc",
    openapi_url: str | None = "/openapi.json",
) -> FastAPI:
    state = ServiceTemplateState(
        config=config,
        metrics=metrics or TenantMetricRegistry(),
        database_settings=config.to_database_settings(),
    )
    app = FastAPI(
        title=title or f"Media Center {config.service_name}",
        version=config.version,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
    )
    app.state.service_template = state

    @app.get("/health")
    def health() -> dict[str, object]:
        _record_operation(
            state,
            tenant_id=PLATFORM_TENANT_ID,
            operation="healthcheck",
            status="success",
            duration_seconds=0.0,
        )
        return {
            "service": state.config.service_name,
            "version": state.config.version,
            "status": "ok",
            "checks": {
                "database": state.database_status(),
                "metrics": state.metrics_status(),
            },
        }

    @app.get(DEFAULT_METRICS_PATH)
    def prometheus_metrics() -> Response:
        if not state.config.prometheus_enabled:
            return Response(status_code=404)

        return Response(
            content=state.metrics.export_prometheus(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.get("/template/context")
    def template_context() -> dict[str, object]:
        started_at = perf_counter()
        context = require_tenant_context()
        _record_operation(
            state,
            tenant_id=context.tenant_id,
            operation="template_context",
            status="success",
            duration_seconds=perf_counter() - started_at,
        )

        return cast(
            dict[str, object],
            RequestContextModel.from_tenant_context(context).model_dump(mode="json"),
        )

    app.add_middleware(
        PublicPathTenantContextMiddleware,
        jwt_secret=config.jwt_secret,
        public_paths=config.public_paths,
        expected_issuer=config.expected_issuer,
        expected_audience=config.expected_audience,
        audit_sink=audit_sink,
    )
    return app


def _record_operation(
    state: ServiceTemplateState,
    *,
    tenant_id: str,
    operation: str,
    status: str,
    duration_seconds: float,
) -> None:
    if not state.config.prometheus_enabled:
        return

    state.metrics.record_operation(
        context=ObservabilityContext(
            tenant_id=tenant_id,
            service_name=state.config.service_name,
            operation=operation,
        ),
        status=status,
        duration_seconds=duration_seconds,
    )


def _normalize_service_name(value: str) -> str:
    return ObservabilityContext(
        tenant_id=PLATFORM_TENANT_ID,
        service_name=value,
        operation="startup",
    ).service_name


def _normalize_version(value: str) -> str:
    normalized = value.strip()
    if normalized == "":
        raise ValueError("version должен быть непустой строкой")

    return normalized


def _normalize_jwt_secret(value: str | bytes) -> str | bytes:
    if isinstance(value, str):
        if value.strip() == "":
            raise ValueError("jwt_secret должен быть непустой строкой")
        return value

    if value.strip() == b"":
        raise ValueError("jwt_secret должен быть непустой bytes-строкой")

    return value


def _normalize_database_url(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = value.strip()
    if normalized == "":
        return None

    return DatabaseSettings(database_url=normalized).database_url


def _normalize_public_paths(paths: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(_normalize_public_path(path) for path in paths)
    if len(normalized) == 0:
        raise ValueError("public_paths должен содержать хотя бы один path")
    if len(set(normalized)) != len(normalized):
        raise ValueError("public_paths не должен содержать дубликаты")

    return normalized


def _normalize_public_path(path: str) -> str:
    normalized = path.strip()
    if normalized == "":
        raise ValueError("public path должен быть непустой строкой")
    if not normalized.startswith("/"):
        raise ValueError("public path должен начинаться с /")
    if normalized != "/":
        normalized = normalized.rstrip("/")

    return normalized


def _is_public_path(path: str, public_paths: Sequence[str]) -> bool:
    normalized = _normalize_public_path(path)
    for public_path in public_paths:
        if normalized == public_path:
            return True
        if public_path != "/" and normalized.startswith(f"{public_path}/"):
            return True

    return False
