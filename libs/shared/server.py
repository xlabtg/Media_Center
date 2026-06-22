from __future__ import annotations

import logging
import platform
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Annotated

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, status

from libs.shared.observability import TenantMetricRegistry
from libs.shared.service_template import (
    ServiceTemplateConfig,
    ServiceTemplateState,
    create_service_app,
)
from libs.shared.tenant import AuditSink

DEFAULT_BASE_APP_PORT = 7700
DEFAULT_BASE_APP_DOCS_URL = "/docs"
DEFAULT_BASE_APP_REDOC_URL = "/redoc"
DEFAULT_BASE_APP_OPENAPI_URL = "/openapi.json"
DEFAULT_BASE_APP_LOG_LEVEL = "INFO"
BASE_APP_SYSTEM_PUBLIC_PATHS = (
    "/ready",
    "/info",
    "/admin/log-level",
)
BASE_APP_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})

LogLevelQuery = Annotated[str, Query(min_length=1)]


def _default_build_metadata() -> dict[str, object]:
    return {
        "build_date": "unknown",
        "git_commit": "unknown",
        "git_tag": "",
        "python": f"Python {platform.python_version()}",
        "python_version": platform.python_version(),
        "python_compiler": platform.python_compiler(),
    }


@dataclass(frozen=True, slots=True)
class BaseAppConfig:
    service: ServiceTemplateConfig
    app_port: int = DEFAULT_BASE_APP_PORT
    build_metadata: Mapping[str, object] = field(
        default_factory=_default_build_metadata,
    )
    docs_url: str | None = DEFAULT_BASE_APP_DOCS_URL
    redoc_url: str | None = DEFAULT_BASE_APP_REDOC_URL
    openapi_url: str | None = DEFAULT_BASE_APP_OPENAPI_URL
    title: str | None = None
    log_level: str = DEFAULT_BASE_APP_LOG_LEVEL

    def __post_init__(self) -> None:
        if not 0 < self.app_port <= 65535:
            raise ValueError("app_port должен быть TCP-портом от 1 до 65535")

        object.__setattr__(
            self,
            "build_metadata",
            dict(self.build_metadata),
        )
        object.__setattr__(
            self,
            "docs_url",
            _normalize_optional_path(self.docs_url),
        )
        object.__setattr__(
            self,
            "redoc_url",
            _normalize_optional_path(self.redoc_url),
        )
        object.__setattr__(
            self,
            "openapi_url",
            _normalize_optional_path(self.openapi_url),
        )
        object.__setattr__(
            self,
            "log_level",
            _normalize_log_level(self.log_level),
        )


@dataclass(slots=True)
class BaseAppState:
    config: BaseAppConfig
    log_level: str


system_router = APIRouter(tags=["system"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])


@system_router.get("/ready")
def ready(request: Request) -> dict[str, object]:
    base_state = _base_app_state(request.app)
    template_state = _service_template_state(request.app)

    return {
        "service": base_state.config.service.service_name,
        "version": base_state.config.service.version,
        "status": "ready",
        "checks": {
            "database": template_state.database_status(),
            "metrics": template_state.metrics_status(),
        },
    }


@system_router.get("/info")
def info(request: Request) -> dict[str, object]:
    base_state = _base_app_state(request.app)
    build = dict(base_state.config.build_metadata)
    payload: dict[str, object] = {
        "service": base_state.config.service.service_name,
        "version": base_state.config.service.version,
        "app_port": base_state.config.app_port,
        "port": base_state.config.app_port,
        "build": build,
    }
    for key, value in build.items():
        payload.setdefault(key, value)

    return payload


@admin_router.get("/log-level")
def get_log_level(request: Request) -> dict[str, str]:
    return {"level": _base_app_state(request.app).log_level}


@admin_router.put("/log-level")
def set_log_level(request: Request, level: LogLevelQuery) -> dict[str, str]:
    try:
        normalized_level = _normalize_log_level(level)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    logging.getLogger().setLevel(normalized_level)
    _base_app_state(request.app).log_level = normalized_level

    return {"level": normalized_level}


def create_base_app(
    config: BaseAppConfig | ServiceTemplateConfig,
    *,
    metrics: TenantMetricRegistry | None = None,
    audit_sink: AuditSink | None = None,
) -> FastAPI:
    base_config = _coerce_base_app_config(config)
    service_config = replace(
        base_config.service,
        public_paths=_base_public_paths(base_config),
    )
    base_config = replace(base_config, service=service_config)

    app = create_service_app(
        service_config,
        title=base_config.title,
        metrics=metrics,
        audit_sink=audit_sink,
        docs_url=base_config.docs_url,
        redoc_url=base_config.redoc_url,
        openapi_url=base_config.openapi_url,
    )
    app.state.base_app = BaseAppState(
        config=base_config,
        log_level=base_config.log_level,
    )
    app.state.service_port = base_config.app_port
    app.include_router(system_router)
    app.include_router(admin_router)
    return app


def _coerce_base_app_config(
    config: BaseAppConfig | ServiceTemplateConfig,
) -> BaseAppConfig:
    if isinstance(config, BaseAppConfig):
        return config

    return BaseAppConfig(service=config)


def _base_public_paths(config: BaseAppConfig) -> tuple[str, ...]:
    paths = [
        *config.service.public_paths,
        *BASE_APP_SYSTEM_PUBLIC_PATHS,
        config.docs_url,
        config.redoc_url,
        config.openapi_url,
    ]
    normalized_paths = (_normalize_path(path) for path in paths if path is not None)
    return tuple(dict.fromkeys(normalized_paths))


def _base_app_state(app: FastAPI) -> BaseAppState:
    state = getattr(app.state, "base_app", None)
    if not isinstance(state, BaseAppState):
        raise RuntimeError("base_app state не инициализирован")

    return state


def _service_template_state(app: FastAPI) -> ServiceTemplateState:
    state = getattr(app.state, "service_template", None)
    if not isinstance(state, ServiceTemplateState):
        raise RuntimeError("service_template state не инициализирован")

    return state


def _normalize_optional_path(value: str | None) -> str | None:
    if value is None:
        return None

    return _normalize_path(value)


def _normalize_path(value: str) -> str:
    normalized = value.strip()
    if normalized == "":
        raise ValueError("path должен быть непустой строкой")
    if not normalized.startswith("/"):
        raise ValueError("path должен начинаться с /")
    if normalized != "/":
        normalized = normalized.rstrip("/")

    return normalized


def _normalize_log_level(value: str) -> str:
    normalized = value.strip().upper()
    if normalized not in BASE_APP_LOG_LEVELS:
        raise ValueError(f"invalid log level: {value}")

    return normalized
