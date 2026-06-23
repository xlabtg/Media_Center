from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from libs.shared import (
    AppSettings,
    AuthMethod,
    EnvSecretProvider,
    S2SConfig,
    VaultSecretProvider,
    VaultSettings,
    load_app_settings,
)
from libs.shared.config import LOG_LEVELS

ROOT = Path(__file__).resolve().parents[1]


def _complete_env(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    env = {
        "APP_ENV": "development",
        "LOG_LEVEL": "INFO",
        "APP_HOST": "0.0.0.0",
        "APP_PORT": "7700",
        "DATABASE_URL": "postgresql+asyncpg://nmc:secret@localhost:5432/nmc",
        "REDIS_URL": "redis://localhost:6379/0",
        "RABBITMQ_URL": "amqp://nmc:secret@localhost:5672/",
        "CHROMA_HOST": "localhost",
        "CHROMA_PORT": "8001",
        "CHROMA_SSL": "false",
        "S3_ENDPOINT_URL": "http://localhost:9000",
        "S3_ACCESS_KEY": "nmc_minio",
        "S3_SECRET_KEY": "minio-secret",
        "S3_BUCKET": "nmc-dev",
        "S3_REGION": "ru-central-1",
        "JWT_SECRET": "local-jwt-secret",
        "JWT_ALGORITHM": "HS256",
        "JWT_ACCESS_TTL_MINUTES": "30",
        "JWT_REFRESH_TTL_DAYS": "14",
        "TOTP_ISSUER": "NMC",
        "TOTP_STEP_SECONDS": "30",
        "TOTP_ALLOWED_DRIFT_STEPS": "1",
        "ENCRYPTION_KEY": "local-encryption-key",
        "API_GATEWAY_RATE_LIMIT": "120",
        "API_GATEWAY_RATE_LIMIT_WINDOW_SECONDS": "60",
        "COUNCIL_CAP_KV": "0.10",
        "VETO_WINDOW_HOURS": "8",
        "BLOCKCHAIN_AUDITOR_URL": "grpc://localhost:50051",
        "PROMETHEUS_ENABLED": "true",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317",
    }
    if overrides is not None:
        env.update(overrides)

    return env


def test_app_settings_loads_typed_env_and_builds_existing_settings() -> None:
    settings = load_app_settings(environ=_complete_env())

    assert isinstance(settings, AppSettings)
    assert settings.app_env == "development"
    assert settings.app_port == 7700
    assert settings.veto_window_hours == 8
    assert settings.jwt_secret.get_secret_value() == "local-jwt-secret"

    assert settings.to_database_settings().database_url == (
        "postgresql+asyncpg://nmc:secret@localhost:5432/nmc"
    )
    assert settings.to_cache_settings().redis_url == "redis://localhost:6379/0"
    assert settings.to_rabbitmq_settings().rabbitmq_url == (
        "amqp://nmc:secret@localhost:5672/"
    )
    assert settings.to_chroma_settings().port == 8001
    assert settings.to_s3_settings().secret_key == "minio-secret"


def test_app_settings_defaults_to_runtime_port_7700_when_env_is_absent() -> None:
    env = _complete_env()
    env.pop("APP_PORT")

    settings = load_app_settings(environ=env)

    assert settings.app_port == 7700


def test_app_settings_allows_env_override_for_app_port_and_log_level() -> None:
    settings = load_app_settings(
        environ=_complete_env(
            {
                "APP_PORT": "7701",
                "LOG_LEVEL": "critical",
            },
        ),
    )

    assert settings.app_port == 7701
    assert settings.log_level == "CRITICAL"
    assert "CRITICAL" in LOG_LEVELS


def test_secret_provider_replaces_placeholders_before_validation() -> None:
    provider = EnvSecretProvider(
        {
            "DATABASE_URL": "postgresql+asyncpg://nmc:vault-db@db:5432/nmc",
            "RABBITMQ_URL": "amqp://nmc:vault-rabbit@rabbitmq:5672/",
            "S3_ACCESS_KEY": "vault-s3-access",
            "S3_SECRET_KEY": "vault-s3-secret",
            "JWT_SECRET": "vault-jwt-secret",
            "ENCRYPTION_KEY": "vault-encryption-key",
        },
    )

    settings = load_app_settings(
        environ=_complete_env(
            {
                "DATABASE_URL": "CHANGE_ME",
                "RABBITMQ_URL": "CHANGE_ME",
                "S3_ACCESS_KEY": "CHANGE_ME",
                "S3_SECRET_KEY": "CHANGE_ME",
                "JWT_SECRET": "CHANGE_ME_USE_LONG_RANDOM_STRING",
                "ENCRYPTION_KEY": "CHANGE_ME_32_BYTES_KEY",
            },
        ),
        secret_provider=provider,
    )

    assert settings.database_url == "postgresql+asyncpg://nmc:vault-db@db:5432/nmc"
    assert settings.rabbitmq_url == "amqp://nmc:vault-rabbit@rabbitmq:5672/"
    assert settings.s3_access_key.get_secret_value() == "vault-s3-access"
    assert settings.s3_secret_key.get_secret_value() == "vault-s3-secret"
    assert settings.jwt_secret.get_secret_value() == "vault-jwt-secret"
    assert settings.encryption_key.get_secret_value() == "vault-encryption-key"


def test_app_settings_builds_s2s_config_from_env() -> None:
    settings = load_app_settings(
        environ=_complete_env(
            {
                "SERVICE_NAME": "api-gateway",
                "S2S_AUTH_METHOD": "rsa_key",
                "S2S_SHARED_SECRET": "env-s2s-secret",
                "S2S_REPLAY_WINDOW_SECONDS": "180",
                "S2S_TOKEN_TTL_SECONDS": "45",
                "K8S_AUTH_ENABLED": "false",
                "S2S_K8S_TOKEN_PATH": "/run/secrets/k8s-token",
                "S2S_AUDIENCE": "nmc-internal",
                "S2S_K8S_ISSUER": "https://kubernetes.default.svc",
                "S2S_K8S_TOKENREVIEW_URL": "https://kubernetes.default.svc/apis/authentication.k8s.io/v1/tokenreviews",
                "S2S_K8S_TOKENREVIEW_TOKEN_PATH": "/run/secrets/tokenreview-token",
                "S2S_K8S_TOKENREVIEW_TIMEOUT_SECONDS": "3.5",
                "S2S_K8S_CA_PATH": "/run/secrets/k8s-ca.crt",
                "S2S_K8S_OIDC_PUBLIC_KEY_PATH": "/run/secrets/k8s-oidc.pem",
                "S2S_RSA_PRIVATE_KEY_PATH": "/run/secrets/s2s-private.pem",
                "S2S_RSA_PUBLIC_KEY_PATH": "/run/secrets/s2s-public.pem",
                "S2S_RSA_ISSUER": "nmc-s2s-stage",
                "S2S_RSA_AUDIENCE": "nmc-s2s-audience",
            },
        ),
    )

    s2s_config = settings.to_s2s_config()

    assert isinstance(s2s_config, S2SConfig)
    assert s2s_config.method is AuthMethod.RSA_KEY
    assert s2s_config.service_name == "api-gateway"
    assert s2s_config.shared_secret == "env-s2s-secret"
    assert s2s_config.replay_window_seconds == 180
    assert s2s_config.token_ttl_seconds == 45
    assert s2s_config.k8s_enabled is False
    assert s2s_config.k8s_token_path == Path("/run/secrets/k8s-token")
    assert s2s_config.k8s_audience == "nmc-internal"
    assert s2s_config.k8s_issuer == "https://kubernetes.default.svc"
    assert s2s_config.k8s_tokenreview_url == (
        "https://kubernetes.default.svc/apis/authentication.k8s.io/v1/tokenreviews"
    )
    assert s2s_config.k8s_tokenreview_token_path == Path(
        "/run/secrets/tokenreview-token",
    )
    assert s2s_config.k8s_tokenreview_timeout_seconds == 3.5
    assert s2s_config.k8s_ca_path == Path("/run/secrets/k8s-ca.crt")
    assert s2s_config.k8s_oidc_public_key_path == Path("/run/secrets/k8s-oidc.pem")
    assert s2s_config.rsa_private_key_path == Path("/run/secrets/s2s-private.pem")
    assert s2s_config.rsa_public_key_path == Path("/run/secrets/s2s-public.pem")
    assert s2s_config.rsa_issuer == "nmc-s2s-stage"
    assert s2s_config.rsa_audience == "nmc-s2s-audience"


def test_s2s_shared_secret_can_come_from_secret_provider() -> None:
    provider = EnvSecretProvider({"S2S_SHARED_SECRET": "vault-s2s-secret"})

    settings = load_app_settings(
        environ=_complete_env({"S2S_SHARED_SECRET": "CHANGE_ME_S2S_SHARED_SECRET"}),
        secret_provider=provider,
    )

    assert settings.s2s_shared_secret is not None
    assert settings.s2s_shared_secret.get_secret_value() == "vault-s2s-secret"
    assert settings.to_s2s_config().shared_secret == "vault-s2s-secret"


def test_vault_secret_provider_reads_hashicorp_kv_v2_payload() -> None:
    captured: dict[str, str] = {}

    def fake_transport(url: str, token: str, timeout_seconds: float) -> bytes:
        captured["url"] = url
        captured["token"] = token
        captured["timeout"] = str(timeout_seconds)
        return json.dumps(
            {
                "data": {
                    "data": {
                        "JWT_SECRET": "vault-jwt-secret",
                        "S3_SECRET_KEY": "vault-s3-secret",
                    },
                },
            },
        ).encode("utf-8")

    provider = VaultSecretProvider(
        VaultSettings.model_validate(
            {
                "enabled": True,
                "address": "https://vault.example.test/",
                "token": "vault-token",
                "mount": "secret",
                "path": "media-center/stage",
                "timeout_seconds": 2.5,
            },
        ),
        transport=fake_transport,
    )

    assert provider.get_secret("JWT_SECRET") == "vault-jwt-secret"
    assert provider.get_secret("S3_SECRET_KEY") == "vault-s3-secret"
    assert provider.get_secret("UNKNOWN") is None
    assert captured == {
        "url": "https://vault.example.test/v1/secret/data/media-center/stage",
        "token": "vault-token",
        "timeout": "2.5",
    }


def test_settings_export_redacts_secrets() -> None:
    settings = load_app_settings(environ=_complete_env())

    redacted = settings.redacted_dict()

    assert redacted["jwt_secret"] == "**********"
    assert redacted["s3_secret_key"] == "**********"
    assert redacted["database_url"] == "**********"
    assert redacted["rabbitmq_url"] == "**********"


def test_env_example_lists_all_app_settings_and_vault_variables() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")

    missing = sorted(
        env_name for env_name in AppSettings.env_names() if env_name not in example
    )

    assert not missing
    assert "VAULT_ENABLED=" in example
    assert "VAULT_ADDR=" in example
    assert "VAULT_TOKEN=" in example
    assert "VAULT_PATH=" in example
