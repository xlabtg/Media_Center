from __future__ import annotations

import os
from collections.abc import Mapping

from wallet import WALLET_SERVICE_NAME

from libs.shared import ServiceTemplateConfig


def build_service_config(
    environ: Mapping[str, str] | None = None,
) -> ServiceTemplateConfig:
    values = os.environ if environ is None else environ
    return ServiceTemplateConfig(
        service_name=_env(values, "SERVICE_NAME", default=WALLET_SERVICE_NAME),
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
