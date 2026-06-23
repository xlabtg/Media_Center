from __future__ import annotations

import os
from collections.abc import Mapping

from libs.shared import (
    DEFAULT_BASE_APP_LOG_LEVEL,
    DEFAULT_BASE_APP_PORT,
    BaseAppConfig,
    ServiceTemplateConfig,
)

MESSENGER_ADAPTER_SERVICE_NAME = "messenger-adapter"
DEFAULT_APP_HOST = "0.0.0.0"


def build_app_host(environ: Mapping[str, str] | None = None) -> str:
    values = os.environ if environ is None else environ
    return _env(values, "APP_HOST", default=DEFAULT_APP_HOST)


def build_base_app_config(
    environ: Mapping[str, str] | None = None,
) -> BaseAppConfig:
    values = os.environ if environ is None else environ
    return BaseAppConfig(
        service=build_service_config(values),
        app_port=_int_env(values, "APP_PORT", default=DEFAULT_BASE_APP_PORT),
        log_level=_env(values, "LOG_LEVEL", default=DEFAULT_BASE_APP_LOG_LEVEL),
    )


def build_service_config(
    environ: Mapping[str, str] | None = None,
) -> ServiceTemplateConfig:
    values = os.environ if environ is None else environ
    return ServiceTemplateConfig(
        service_name=_env(
            values,
            "SERVICE_NAME",
            default=MESSENGER_ADAPTER_SERVICE_NAME,
        ),
        version=_env(values, "SERVICE_VERSION", default="0.1.0"),
        database_url=_optional_env(values, "DATABASE_URL"),
        redis_url=_optional_env(values, "REDIS_URL"),
        rabbitmq_url=_optional_env(values, "RABBITMQ_URL"),
        jwt_secret=_required_env(values, "JWT_SECRET"),
        prometheus_enabled=_bool_env(values, "PROMETHEUS_ENABLED", default=True),
    )


def _env(values: Mapping[str, str], name: str, *, default: str) -> str:
    value = values.get(name)
    if value is None or value.strip() == "":
        return default

    return value.strip()


def _optional_env(values: Mapping[str, str], name: str) -> str | None:
    value = values.get(name)
    if value is None or value.strip() == "":
        return None

    return value.strip()


def _required_env(values: Mapping[str, str], name: str) -> str:
    value = values.get(name)
    if value is None or value.strip() == "":
        raise ValueError(f"{name} должен быть задан")

    return value.strip()


def _bool_env(values: Mapping[str, str], name: str, *, default: bool) -> bool:
    value = values.get(name)
    if value is None or value.strip() == "":
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ValueError(f"{name} должен быть boolean")


def _int_env(values: Mapping[str, str], name: str, *, default: int) -> int:
    value = values.get(name)
    if value is None or value.strip() == "":
        return default

    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} должен быть целым числом") from exc
