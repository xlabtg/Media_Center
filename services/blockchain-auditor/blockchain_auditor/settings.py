from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

from libs.shared.config import ConfigServerTransport, resolve_config_values

BLOCKCHAIN_AUDITOR_SERVICE_NAME = "blockchain-auditor"
DEFAULT_BLOCKCHAIN_AUDITOR_URL = "grpc://localhost:50051"
BLOCKCHAIN_AUDITOR_URL_ENV = "BLOCKCHAIN_AUDITOR_URL"


@dataclass(frozen=True, slots=True)
class BlockchainAuditorSettings:
    blockchain_auditor_url: str = DEFAULT_BLOCKCHAIN_AUDITOR_URL
    service_name: str = BLOCKCHAIN_AUDITOR_SERVICE_NAME
    version: str = "0.1.0"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "blockchain_auditor_url",
            _normalize_grpc_url(self.blockchain_auditor_url),
        )
        object.__setattr__(
            self,
            "service_name",
            _normalize_required_string(self.service_name, name="SERVICE_NAME"),
        )
        object.__setattr__(
            self,
            "version",
            _normalize_required_string(self.version, name="SERVICE_VERSION"),
        )


def build_blockchain_auditor_settings(
    environ: Mapping[str, str] | None = None,
    *,
    config_server_transport: ConfigServerTransport | None = None,
) -> BlockchainAuditorSettings:
    raw_values = os.environ if environ is None else environ
    values = resolve_config_values(
        raw_values,
        application=BLOCKCHAIN_AUDITOR_SERVICE_NAME,
        config_server_transport=config_server_transport,
    )
    return BlockchainAuditorSettings(
        blockchain_auditor_url=_env(
            values,
            BLOCKCHAIN_AUDITOR_URL_ENV,
            default=DEFAULT_BLOCKCHAIN_AUDITOR_URL,
        ),
        service_name=_env(
            values,
            "SERVICE_NAME",
            default=BLOCKCHAIN_AUDITOR_SERVICE_NAME,
        ),
        version=_env(values, "SERVICE_VERSION", default="0.1.0"),
    )


def _env(values: Mapping[str, str], name: str, *, default: str) -> str:
    value = values.get(name)
    if value is None or value.strip() == "":
        return default

    return value.strip()


def _normalize_grpc_url(value: str) -> str:
    normalized = _normalize_required_string(
        value,
        name=BLOCKCHAIN_AUDITOR_URL_ENV,
    ).rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"grpc", "grpcs"}:
        raise ValueError(
            "BLOCKCHAIN_AUDITOR_URL должен использовать grpc:// или grpcs://"
        )
    if parsed.netloc == "":
        raise ValueError("BLOCKCHAIN_AUDITOR_URL должен содержать host:port")

    return normalized


def _normalize_required_string(value: str, *, name: str) -> str:
    normalized = value.strip()
    if normalized == "":
        raise ValueError(f"{name} должен быть непустой строкой")

    return normalized
