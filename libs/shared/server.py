from __future__ import annotations

import json
import logging
import os
import platform
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import perf_counter

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from libs.shared.config import (
    LOG_LEVELS as CONFIG_LOG_LEVELS,
)
from libs.shared.config import (
    ConfigServerTransport,
    resolve_config_values,
)
from libs.shared.logging_config import setup_logging
from libs.shared.observability import (
    DEFAULT_METRICS_PATH,
    ObservabilityContext,
    TenantMetricRegistry,
)
from libs.shared.s2s_auth import S2SConfig, get_s2s_auth, require_s2s
from libs.shared.service_template import (
    PLATFORM_TENANT_ID,
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
DEFAULT_BUILD_INFO_PATH = Path("/app/config/build_info.json")
DEFAULT_RUNTIME_APP_HOST = "0.0.0.0"
DEFAULT_RUNTIME_CONFIG_SERVER_PROJECT = "media-center"
DEFAULT_RUNTIME_CONFIG_SERVER_FRAMEWORK = "fastapi"
BASE_APP_HTTP_METRICS_OPERATION = "http_request"
BASE_APP_SYSTEM_PUBLIC_PATHS = (
    "/ready",
    "/info",
    "/admin",
)
BASE_APP_LOG_LEVELS = CONFIG_LOG_LEVELS


class LogLevelUpdateRequest(BaseModel):
    level: str

    @field_validator("level")
    @classmethod
    def _validate_level(cls, value: str) -> str:
        return _normalize_log_level(value)


def _default_build_metadata(service: ServiceTemplateConfig) -> dict[str, object]:
    git_tag = _env("GIT_TAG", default="")
    version_default = git_tag or service.version
    return {
        "service": _env("SERVICE_NAME", default=service.service_name),
        "version": _env("SERVICE_VERSION", default=version_default),
        "build_date": _env("BUILD_DATE", default="unknown"),
        "git_commit": _env("GIT_COMMIT", default="unknown"),
        "git_tag": git_tag,
        "python": f"Python {platform.python_version()}",
        "python_version": platform.python_version(),
        "python_compiler": platform.python_compiler(),
    }


@dataclass(frozen=True, slots=True)
class BaseAppConfig:
    service: ServiceTemplateConfig
    app_port: int = DEFAULT_BASE_APP_PORT
    build_metadata: Mapping[str, object] = field(default_factory=dict)
    build_info_path: str | Path | None = DEFAULT_BUILD_INFO_PATH
    docs_url: str | None = DEFAULT_BASE_APP_DOCS_URL
    redoc_url: str | None = DEFAULT_BASE_APP_REDOC_URL
    openapi_url: str | None = DEFAULT_BASE_APP_OPENAPI_URL
    title: str | None = None
    log_level: str = DEFAULT_BASE_APP_LOG_LEVEL
    s2s: S2SConfig = field(default_factory=S2SConfig.from_env)

    def __post_init__(self) -> None:
        if not 0 < self.app_port <= 65535:
            raise ValueError("app_port должен быть TCP-портом от 1 до 65535")

        build_info_path = _normalize_build_info_path(self.build_info_path)
        build_metadata = _resolve_build_metadata(
            service=self.service,
            explicit_metadata=self.build_metadata,
            build_info_path=build_info_path,
        )
        object.__setattr__(self, "build_info_path", build_info_path)
        object.__setattr__(
            self,
            "build_metadata",
            build_metadata,
        )
        object.__setattr__(
            self,
            "service",
            _service_config_from_build_metadata(self.service, build_metadata),
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
admin_router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_s2s)],
)


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
def set_log_level(request: Request, payload: LogLevelUpdateRequest) -> dict[str, str]:
    normalized_level = payload.level
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
    setup_logging(
        base_config.log_level,
        service_name=base_config.service.service_name,
    )

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
    app.state.s2s_auth = get_s2s_auth(base_config.s2s)
    app.state.service_port = base_config.app_port
    _install_admin_s2s_guard(app)
    app.include_router(system_router)
    app.include_router(admin_router)
    _install_base_http_metrics(app)
    return app


def create_service_runtime_app(
    config: BaseAppConfig | ServiceTemplateConfig,
    *,
    title: str,
    metrics: TenantMetricRegistry | None = None,
    audit_sink: AuditSink | None = None,
) -> FastAPI:
    if isinstance(config, BaseAppConfig):
        base_config = (
            config if config.title is not None else replace(config, title=title)
        )
        return create_base_app(
            base_config,
            metrics=metrics,
            audit_sink=audit_sink,
        )

    return create_service_app(
        config,
        title=title,
        metrics=metrics,
        audit_sink=audit_sink,
    )


def build_runtime_app_host(
    environ: Mapping[str, str] | None = None,
    *,
    application: str | None = None,
    config_server_transport: ConfigServerTransport | None = None,
) -> str:
    raw_values = os.environ if environ is None else environ
    values = resolve_config_values(
        raw_values,
        application=_runtime_application(raw_values, application),
        project=DEFAULT_RUNTIME_CONFIG_SERVER_PROJECT,
        framework=DEFAULT_RUNTIME_CONFIG_SERVER_FRAMEWORK,
        config_server_transport=config_server_transport,
    )
    return _mapping_env(values, "APP_HOST", default=DEFAULT_RUNTIME_APP_HOST)


def build_runtime_base_app_config(
    service: ServiceTemplateConfig,
    *,
    environ: Mapping[str, str] | None = None,
    config_server_transport: ConfigServerTransport | None = None,
) -> BaseAppConfig:
    raw_values = os.environ if environ is None else environ
    values = resolve_config_values(
        raw_values,
        application=service.service_name,
        project=DEFAULT_RUNTIME_CONFIG_SERVER_PROJECT,
        framework=DEFAULT_RUNTIME_CONFIG_SERVER_FRAMEWORK,
        config_server_transport=config_server_transport,
    )
    return BaseAppConfig(
        service=service,
        app_port=_mapping_int_env(values, "APP_PORT", default=DEFAULT_BASE_APP_PORT),
        log_level=_mapping_env(
            values,
            "LOG_LEVEL",
            default=DEFAULT_BASE_APP_LOG_LEVEL,
        ),
    )


def _runtime_application(
    values: Mapping[str, str],
    application: str | None,
) -> str:
    raw_application = (
        application if application is not None else values.get("SERVICE_NAME")
    )
    normalized = "" if raw_application is None else raw_application.strip().strip("/")
    if normalized == "":
        return "media-center"

    return normalized


def _coerce_base_app_config(
    config: BaseAppConfig | ServiceTemplateConfig,
) -> BaseAppConfig:
    if isinstance(config, BaseAppConfig):
        return config

    return BaseAppConfig(service=config)


def _resolve_build_metadata(
    *,
    service: ServiceTemplateConfig,
    explicit_metadata: Mapping[str, object],
    build_info_path: Path | None,
) -> dict[str, object]:
    fallback = _default_build_metadata(service)
    explicit = _normalize_build_metadata(explicit_metadata)
    file_metadata = _load_build_info(build_info_path)
    overlay = {**explicit, **file_metadata}
    metadata = {**fallback, **overlay}
    if "version" not in overlay:
        git_tag = _build_metadata_text(overlay, "git_tag")
        if git_tag is not None:
            metadata["version"] = git_tag

    return metadata


def _load_build_info(path: Path | None) -> dict[str, object]:
    if path is None or not path.is_file():
        return {}

    try:
        raw_metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}

    if not isinstance(raw_metadata, dict):
        return {}

    return _normalize_build_metadata(raw_metadata)


def _normalize_build_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key, value in metadata.items():
        normalized_key = str(key).strip()
        if normalized_key:
            normalized[normalized_key] = value

    return normalized


def _service_config_from_build_metadata(
    service: ServiceTemplateConfig,
    metadata: Mapping[str, object],
) -> ServiceTemplateConfig:
    service_name = _build_metadata_text(metadata, "service") or service.service_name
    version = (
        _build_metadata_text(metadata, "version")
        or _build_metadata_text(metadata, "git_tag")
        or service.version
    )
    if service_name == service.service_name and version == service.version:
        return service

    return replace(service, service_name=service_name, version=version)


def _normalize_build_info_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None

    return Path(value)


def _build_metadata_text(
    metadata: Mapping[str, object],
    key: str,
) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None

    normalized = str(value).strip()
    if normalized == "":
        return None

    return normalized


def _env(name: str, *, default: str) -> str:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default

    return value.strip()


def _mapping_env(values: Mapping[str, str], name: str, *, default: str) -> str:
    value = values.get(name)
    if value is None or value.strip() == "":
        return default

    return value.strip()


def _mapping_int_env(values: Mapping[str, str], name: str, *, default: int) -> int:
    value = values.get(name)
    if value is None or value.strip() == "":
        return default

    return int(value.strip())


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


def _install_base_http_metrics(app: FastAPI) -> None:
    @app.middleware("http")
    async def base_http_metrics(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        state = _service_template_state(request.app)
        if (
            not state.config.prometheus_enabled
            or request.url.path == DEFAULT_METRICS_PATH
        ):
            return await call_next(request)

        started_at = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            _record_base_http_metric(
                state,
                status="error",
                duration_seconds=perf_counter() - started_at,
            )
            raise

        _record_base_http_metric(
            state,
            status=_base_http_metric_status(response.status_code),
            duration_seconds=perf_counter() - started_at,
        )
        return response


def _install_admin_s2s_guard(app: FastAPI) -> None:
    @app.middleware("http")
    async def base_admin_s2s_guard(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not _is_admin_path(request.url.path):
            return await call_next(request)

        try:
            require_s2s(request)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=exc.headers,
            )

        return await call_next(request)


def _record_base_http_metric(
    state: ServiceTemplateState,
    *,
    status: str,
    duration_seconds: float,
) -> None:
    state.metrics.record_operation(
        context=ObservabilityContext(
            tenant_id=PLATFORM_TENANT_ID,
            service_name=state.config.service_name,
            operation=BASE_APP_HTTP_METRICS_OPERATION,
        ),
        status=status,
        duration_seconds=duration_seconds,
    )


def _base_http_metric_status(status_code: int) -> str:
    if status_code >= 400:
        return "error"

    return "success"


def _is_admin_path(path: str) -> bool:
    return path == "/admin" or path.startswith("/admin/")


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
