from __future__ import annotations

import os
from collections.abc import Mapping

from libs.shared.config import ConfigServerTransport, resolve_config_values
from libs.shared.service_template import ServiceTemplateConfig


def build_service_config(
    environ: Mapping[str, str] | None = None,
    *,
    config_server_transport: ConfigServerTransport | None = None,
) -> ServiceTemplateConfig:
    raw_values = os.environ if environ is None else environ
    values = resolve_config_values(
        raw_values,
        application="service-template",
        config_server_transport=config_server_transport,
    )
    return ServiceTemplateConfig(
        service_name=_env(values, "SERVICE_NAME", default="service-template"),
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
