from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Protocol, Self, cast
from urllib.parse import quote
from urllib.request import Request, urlopen

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from libs.shared.cache import CacheSettings, validate_redis_url
from libs.shared.db import DatabaseSettings, validate_database_url
from libs.shared.events import (
    RabbitMQSettings,
    validate_rabbitmq_url,
)
from libs.shared.object_storage import (
    S3Settings,
    validate_s3_bucket_name,
    validate_s3_endpoint_url,
)
from libs.shared.vector import (
    ChromaSettings,
    validate_chroma_host,
    validate_chroma_port,
)

APP_SETTINGS_ENV_NAMES = (
    "APP_ENV",
    "LOG_LEVEL",
    "APP_HOST",
    "APP_PORT",
    "DATABASE_URL",
    "REDIS_URL",
    "RABBITMQ_URL",
    "CHROMA_HOST",
    "CHROMA_PORT",
    "CHROMA_SSL",
    "S3_ENDPOINT_URL",
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
    "S3_BUCKET",
    "S3_REGION",
    "JWT_SECRET",
    "JWT_ALGORITHM",
    "JWT_ACCESS_TTL_MINUTES",
    "JWT_REFRESH_TTL_DAYS",
    "TOTP_ISSUER",
    "TOTP_STEP_SECONDS",
    "TOTP_ALLOWED_DRIFT_STEPS",
    "ENCRYPTION_KEY",
    "API_GATEWAY_RATE_LIMIT",
    "API_GATEWAY_RATE_LIMIT_WINDOW_SECONDS",
    "COUNCIL_CAP_KV",
    "VETO_WINDOW_HOURS",
    "BLOCKCHAIN_AUDITOR_URL",
    "PROMETHEUS_ENABLED",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
)
SECRET_ENV_NAMES = (
    "DATABASE_URL",
    "RABBITMQ_URL",
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
    "JWT_SECRET",
    "ENCRYPTION_KEY",
)
VAULT_ENV_NAMES = (
    "VAULT_ENABLED",
    "VAULT_ADDR",
    "VAULT_TOKEN",
    "VAULT_MOUNT",
    "VAULT_PATH",
    "VAULT_TIMEOUT_SECONDS",
)
SECRET_FIELD_NAMES = frozenset(
    {
        "database_url",
        "rabbitmq_url",
        "s3_access_key",
        "s3_secret_key",
        "jwt_secret",
        "encryption_key",
    },
)
LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})
APP_ENVS = frozenset({"development", "staging", "production"})
REDACTED_SECRET = "**********"

VaultTransport = Callable[[str, str, float], bytes]


class SecretProvider(Protocol):
    def get_secret(self, name: str) -> str | None:
        """Return a secret value by environment variable name."""


class EnvSecretProvider:
    def __init__(self, secrets: Mapping[str, str]) -> None:
        self._secrets = dict(secrets)

    def get_secret(self, name: str) -> str | None:
        value = self._secrets.get(name)
        if value is None or value.strip() == "":
            return None

        return value


class VaultSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    enabled: bool = Field(default=False, validation_alias="VAULT_ENABLED")
    address: str | None = Field(default=None, validation_alias="VAULT_ADDR")
    token: SecretStr | None = Field(default=None, validation_alias="VAULT_TOKEN")
    mount: str = Field(default="secret", validation_alias="VAULT_MOUNT")
    path: str | None = Field(default=None, validation_alias="VAULT_PATH")
    timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        validation_alias="VAULT_TIMEOUT_SECONDS",
    )

    @field_validator("address")
    @classmethod
    def _normalize_address(cls, value: str | None) -> str | None:
        if value is None:
            return None

        normalized = value.strip().rstrip("/")
        if normalized == "":
            return None
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("VAULT_ADDR должен использовать http:// или https://")

        return normalized

    @field_validator("mount")
    @classmethod
    def _normalize_mount(cls, value: str) -> str:
        normalized = value.strip().strip("/")
        if normalized == "":
            raise ValueError("VAULT_MOUNT должен быть непустой строкой")

        return normalized

    @field_validator("path")
    @classmethod
    def _normalize_path(cls, value: str | None) -> str | None:
        if value is None:
            return None

        normalized = value.strip().strip("/")
        if normalized == "":
            return None

        return normalized

    @model_validator(mode="after")
    def _require_enabled_fields(self) -> Self:
        if not self.enabled:
            return self
        if self.address is None:
            raise ValueError("VAULT_ADDR должен быть задан при VAULT_ENABLED=true")
        if self.token is None or self.token.get_secret_value().strip() == "":
            raise ValueError("VAULT_TOKEN должен быть задан при VAULT_ENABLED=true")
        if self.path is None:
            raise ValueError("VAULT_PATH должен быть задан при VAULT_ENABLED=true")

        return self

    def token_value(self) -> str:
        if self.token is None:
            raise ValueError("VAULT_TOKEN должен быть задан")

        return self.token.get_secret_value()


class VaultSecretProvider:
    def __init__(
        self,
        settings: VaultSettings,
        *,
        transport: VaultTransport | None = None,
    ) -> None:
        if not settings.enabled:
            raise ValueError("VaultSecretProvider требует VAULT_ENABLED=true")

        self._settings = settings
        self._transport = transport or _http_vault_get
        self._cache: dict[str, str] | None = None

    def get_secret(self, name: str) -> str | None:
        return self._secrets().get(name)

    def _secrets(self) -> dict[str, str]:
        if self._cache is None:
            self._cache = _vault_payload_to_secrets(
                self._transport(
                    _vault_kv2_url(self._settings),
                    self._settings.token_value(),
                    self._settings.timeout_seconds,
                ),
            )

        return self._cache


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_env: str = Field(default="development", validation_alias="APP_ENV")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    app_host: str = Field(default="0.0.0.0", validation_alias="APP_HOST")
    app_port: int = Field(default=8000, gt=0, le=65535, validation_alias="APP_PORT")

    database_url: str = Field(validation_alias="DATABASE_URL")
    redis_url: str = Field(validation_alias="REDIS_URL")
    rabbitmq_url: str = Field(validation_alias="RABBITMQ_URL")

    chroma_host: str = Field(validation_alias="CHROMA_HOST")
    chroma_port: int = Field(default=8001, validation_alias="CHROMA_PORT")
    chroma_ssl: bool = Field(default=False, validation_alias="CHROMA_SSL")

    s3_endpoint_url: str = Field(validation_alias="S3_ENDPOINT_URL")
    s3_access_key: SecretStr = Field(validation_alias="S3_ACCESS_KEY")
    s3_secret_key: SecretStr = Field(validation_alias="S3_SECRET_KEY")
    s3_bucket: str = Field(validation_alias="S3_BUCKET")
    s3_region: str = Field(default="ru-central-1", validation_alias="S3_REGION")

    jwt_secret: SecretStr = Field(validation_alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", validation_alias="JWT_ALGORITHM")
    jwt_access_ttl_minutes: int = Field(
        default=30,
        gt=0,
        validation_alias="JWT_ACCESS_TTL_MINUTES",
    )
    jwt_refresh_ttl_days: int = Field(
        default=14,
        gt=0,
        validation_alias="JWT_REFRESH_TTL_DAYS",
    )
    totp_issuer: str = Field(default="NMC", validation_alias="TOTP_ISSUER")
    totp_step_seconds: int = Field(
        default=30,
        gt=0,
        validation_alias="TOTP_STEP_SECONDS",
    )
    totp_allowed_drift_steps: int = Field(
        default=1,
        ge=0,
        validation_alias="TOTP_ALLOWED_DRIFT_STEPS",
    )
    encryption_key: SecretStr = Field(validation_alias="ENCRYPTION_KEY")

    api_gateway_rate_limit: int = Field(
        default=120,
        gt=0,
        validation_alias="API_GATEWAY_RATE_LIMIT",
    )
    api_gateway_rate_limit_window_seconds: int = Field(
        default=60,
        gt=0,
        validation_alias="API_GATEWAY_RATE_LIMIT_WINDOW_SECONDS",
    )

    council_cap_kv: float = Field(
        default=0.10,
        ge=0,
        le=1,
        validation_alias="COUNCIL_CAP_KV",
    )
    veto_window_hours: int = Field(
        default=8,
        gt=0,
        validation_alias="VETO_WINDOW_HOURS",
    )

    blockchain_auditor_url: str = Field(
        default="grpc://localhost:50051",
        validation_alias="BLOCKCHAIN_AUDITOR_URL",
    )
    prometheus_enabled: bool = Field(
        default=True,
        validation_alias="PROMETHEUS_ENABLED",
    )
    otel_exporter_otlp_endpoint: str = Field(
        default="http://localhost:4317",
        validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT",
    )

    @classmethod
    def env_names(cls) -> tuple[str, ...]:
        return APP_SETTINGS_ENV_NAMES

    @field_validator("app_env")
    @classmethod
    def _validate_app_env(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in APP_ENVS:
            raise ValueError("APP_ENV должен быть development, staging или production")

        return normalized

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in LOG_LEVELS:
            raise ValueError("LOG_LEVEL должен быть DEBUG, INFO, WARNING или ERROR")

        return normalized

    @field_validator("app_host", "totp_issuer", "blockchain_auditor_url")
    @classmethod
    def _validate_required_string(cls, value: str) -> str:
        normalized = value.strip()
        if normalized == "":
            raise ValueError("значение должно быть непустой строкой")

        return normalized

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        return validate_database_url(value)

    @field_validator("redis_url")
    @classmethod
    def _validate_redis_url(cls, value: str) -> str:
        return validate_redis_url(value)

    @field_validator("rabbitmq_url")
    @classmethod
    def _validate_rabbitmq_url(cls, value: str) -> str:
        return validate_rabbitmq_url(value)

    @field_validator("chroma_host")
    @classmethod
    def _validate_chroma_host(cls, value: str) -> str:
        return validate_chroma_host(value)

    @field_validator("chroma_port")
    @classmethod
    def _validate_chroma_port(cls, value: int) -> int:
        return validate_chroma_port(value)

    @field_validator("s3_endpoint_url")
    @classmethod
    def _validate_s3_endpoint_url(cls, value: str) -> str:
        return validate_s3_endpoint_url(value)

    @field_validator("s3_bucket")
    @classmethod
    def _validate_s3_bucket(cls, value: str) -> str:
        return validate_s3_bucket_name(value)

    @field_validator("s3_region")
    @classmethod
    def _validate_s3_region(cls, value: str) -> str:
        normalized = value.strip()
        if normalized == "":
            raise ValueError("S3_REGION должен быть непустой строкой")

        return normalized

    @field_validator("jwt_algorithm")
    @classmethod
    def _validate_jwt_algorithm(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized != "HS256":
            raise ValueError("JWT_ALGORITHM должен быть HS256")

        return normalized

    @field_validator(
        "s3_access_key",
        "s3_secret_key",
        "jwt_secret",
        "encryption_key",
    )
    @classmethod
    def _validate_secret(cls, value: SecretStr) -> SecretStr:
        if value.get_secret_value().strip() == "":
            raise ValueError("секрет должен быть непустой строкой")

        return value

    def to_database_settings(self) -> DatabaseSettings:
        return DatabaseSettings(database_url=self.database_url)

    def to_cache_settings(self) -> CacheSettings:
        return CacheSettings(redis_url=self.redis_url)

    def to_rabbitmq_settings(self) -> RabbitMQSettings:
        return RabbitMQSettings(rabbitmq_url=self.rabbitmq_url)

    def to_chroma_settings(self) -> ChromaSettings:
        return ChromaSettings(
            host=self.chroma_host,
            port=self.chroma_port,
            ssl=self.chroma_ssl,
            environment=self.app_env,
        )

    def to_s3_settings(self) -> S3Settings:
        return S3Settings(
            endpoint_url=self.s3_endpoint_url,
            access_key=self.s3_access_key.get_secret_value(),
            secret_key=self.s3_secret_key.get_secret_value(),
            bucket=self.s3_bucket,
            region=self.s3_region,
        )

    def redacted_dict(self) -> dict[str, object]:
        data = self.model_dump(mode="json")
        for field_name in SECRET_FIELD_NAMES:
            data[field_name] = REDACTED_SECRET

        return cast(dict[str, object], data)


def load_app_settings(
    environ: Mapping[str, str] | None = None,
    *,
    secret_provider: SecretProvider | None = None,
) -> AppSettings:
    if environ is None:
        if secret_provider is None:
            vault_settings = VaultSettings()
            if not vault_settings.enabled:
                return _app_settings_from_sources()

            secret_provider = VaultSecretProvider(vault_settings)

        return _app_settings_from_init(_secret_values_from_provider(secret_provider))

    values = dict(environ)
    if secret_provider is None:
        vault_settings = VaultSettings.model_validate(values)
        if vault_settings.enabled:
            secret_provider = VaultSecretProvider(vault_settings)

    if secret_provider is not None:
        values.update(_missing_secret_values(values, secret_provider))

    return AppSettings.model_validate(values)


def _app_settings_from_sources() -> AppSettings:
    return AppSettings()  # type: ignore[call-arg]


def _app_settings_from_init(values: Mapping[str, str]) -> AppSettings:
    return AppSettings(**dict(values))  # type: ignore[arg-type]


def _missing_secret_values(
    values: Mapping[str, str],
    secret_provider: SecretProvider,
) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for env_name in SECRET_ENV_NAMES:
        if not _needs_secret_provider(values.get(env_name)):
            continue

        secret_value = secret_provider.get_secret(env_name)
        if secret_value is not None and secret_value.strip() != "":
            resolved[env_name] = secret_value

    return resolved


def _secret_values_from_provider(secret_provider: SecretProvider) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for env_name in SECRET_ENV_NAMES:
        secret_value = secret_provider.get_secret(env_name)
        if secret_value is not None and secret_value.strip() != "":
            resolved[env_name] = secret_value

    return resolved


def _needs_secret_provider(value: str | None) -> bool:
    if value is None or value.strip() == "":
        return True

    return value.strip().upper().startswith("CHANGE_ME")


def _vault_kv2_url(settings: VaultSettings) -> str:
    if settings.address is None or settings.path is None:
        raise ValueError("VAULT_ADDR и VAULT_PATH должны быть заданы")

    path = "/".join(
        quote(segment, safe="")
        for segment in (
            settings.mount,
            "data",
            *settings.path.split("/"),
        )
        if segment != ""
    )
    return f"{settings.address}/v1/{path}"


def _vault_payload_to_secrets(raw_payload: bytes) -> dict[str, str]:
    decoded = json.loads(raw_payload.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("Vault response должен быть JSON object")

    raw_data = decoded.get("data")
    if not isinstance(raw_data, dict):
        raise ValueError("Vault response должен содержать data object")

    nested_data = raw_data.get("data")
    secret_values = nested_data if isinstance(nested_data, dict) else raw_data

    result: dict[str, str] = {}
    for key, value in secret_values.items():
        if isinstance(key, str) and _is_secret_scalar(value):
            result[key] = str(value)

    return result


def _is_secret_scalar(value: object) -> bool:
    return isinstance(value, str | int | float | bool)


def _http_vault_get(url: str, token: str, timeout_seconds: float) -> bytes:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "X-Vault-Token": token,
        },
        method="GET",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        return cast(bytes, response.read())
