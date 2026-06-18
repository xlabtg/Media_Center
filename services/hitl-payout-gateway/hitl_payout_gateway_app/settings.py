from __future__ import annotations

import os
from collections.abc import Mapping

from hitl_payout_gateway import HITL_PAYOUT_GATEWAY_SERVICE_NAME

from libs.shared import ServiceTemplateConfig


def build_service_config(
    environ: Mapping[str, str] | None = None,
) -> ServiceTemplateConfig:
    values = os.environ if environ is None else environ
    return ServiceTemplateConfig(
        service_name=_env(
            values,
            "SERVICE_NAME",
            default=HITL_PAYOUT_GATEWAY_SERVICE_NAME,
        ),
        version=_env(values, "SERVICE_VERSION", default="0.1.0"),
        database_url=_optional_env(values, "DATABASE_URL"),
        jwt_secret=_required_env(values, "JWT_SECRET"),
        prometheus_enabled=_bool_env(values, "PROMETHEUS_ENABLED", default=True),
    )


def build_totp_secrets(
    environ: Mapping[str, str] | None = None,
) -> dict[tuple[str, str], str]:
    values = os.environ if environ is None else environ
    tenant_id = _optional_env(values, "HITL_TOTP_TENANT_ID")
    subject = _optional_env(values, "HITL_TOTP_SUBJECT")
    secret = _optional_env(values, "HITL_TOTP_SECRET")
    if tenant_id is None and subject is None and secret is None:
        return {}
    if tenant_id is None or subject is None or secret is None:
        raise ValueError(
            "HITL_TOTP_TENANT_ID, HITL_TOTP_SUBJECT и HITL_TOTP_SECRET "
            "должны быть заданы вместе"
        )

    return {(tenant_id, subject): secret}


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
