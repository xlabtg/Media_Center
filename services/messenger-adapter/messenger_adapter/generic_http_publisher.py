from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

import httpx
from pydantic import Field, ValidationError, field_validator

from libs.shared.models import JSONValue, SharedBaseModel
from messenger_adapter.base_adapter import (
    PlatformPublicationError,
    PlatformPublishCommand,
    PlatformPublishResult,
)
from messenger_adapter.content_transformer import (
    clone_json_object,
    media_items_from_metadata,
)
from messenger_adapter.platform_http import response_json_object, retry_after_seconds
from messenger_adapter.platform_registry import (
    PlatformNotRegisteredError,
    PlatformRegistry,
    PlatformStatus,
)

HTTP_METHOD = Literal["POST", "PUT", "PATCH"]
HTTP_PAYLOAD_FORMAT = Literal["json", "form"]
HTTP_AUTH_MODE = Literal["bearer", "oauth", "header", "query", "form", "json", "none"]

DEFAULT_RESPONSE_REF_FIELDS = (
    "publication_id",
    "post_id",
    "article_id",
    "topic_id",
    "id",
    "url",
)
DEFAULT_PUBLISHED_AT_FIELDS = ("published_at", "created_at", "date")
DEFAULT_RATE_LIMIT_CODES = ("rate_limited", "too_many_requests", "429")
DEFAULT_RETRYABLE_CODES = (
    "platform_timeout",
    "platform_unavailable",
    "temporarily_unavailable",
    "timeout",
    "500",
    "502",
    "503",
    "504",
)
DEFAULT_AUTH_CODES = ("auth_failed", "unauthorized", "401")
DEFAULT_ACCESS_DENIED_CODES = ("access_denied", "forbidden", "403")


class RegistryHTTPConfig(SharedBaseModel):
    endpoint_url: str = Field(min_length=1, max_length=2048)
    method: HTTP_METHOD = "POST"
    payload_format: HTTP_PAYLOAD_FORMAT = "json"
    auth_mode: HTTP_AUTH_MODE = "bearer"
    auth_header: str = Field(default="Authorization", min_length=1, max_length=128)
    auth_scheme: str | None = Field(default=None, min_length=1, max_length=64)
    token_field: str = Field(default="access_token", min_length=1, max_length=128)
    target_field: str = Field(default="target_id", min_length=1, max_length=128)
    content_field: str = Field(default="content", min_length=1, max_length=128)
    media_field: str | None = Field(default=None, min_length=1, max_length=128)
    metadata_field: str | None = Field(default=None, min_length=1, max_length=128)
    publication_id_field: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    correlation_id_field: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    idempotency_header: str | None = Field(default=None, min_length=1, max_length=128)
    headers: dict[str, str] = Field(default_factory=dict)
    query_params: dict[str, str] = Field(default_factory=dict)
    extra_payload: dict[str, JSONValue] = Field(default_factory=dict)
    response_ref_fields: tuple[str, ...] = Field(
        default=DEFAULT_RESPONSE_REF_FIELDS,
        min_length=1,
    )
    published_at_fields: tuple[str, ...] = Field(
        default=DEFAULT_PUBLISHED_AT_FIELDS,
        min_length=1,
    )
    rate_limit_error_codes: tuple[str, ...] = DEFAULT_RATE_LIMIT_CODES
    retryable_error_codes: tuple[str, ...] = DEFAULT_RETRYABLE_CODES
    auth_error_codes: tuple[str, ...] = DEFAULT_AUTH_CODES
    access_denied_error_codes: tuple[str, ...] = DEFAULT_ACCESS_DENIED_CODES

    @field_validator("method", mode="before")
    @classmethod
    def _normalize_method(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("payload_format", "auth_mode", mode="before")
    @classmethod
    def _normalize_lowercase(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator(
        "response_ref_fields",
        "published_at_fields",
        "rate_limit_error_codes",
        "retryable_error_codes",
        "auth_error_codes",
        "access_denied_error_codes",
        mode="before",
    )
    @classmethod
    def _normalize_string_tuple(cls, value: object) -> object:
        if isinstance(value, str):
            return (value.strip(),)
        if isinstance(value, list | tuple):
            normalized = []
            for item in value:
                if not isinstance(item, str):
                    return value
                stripped = item.strip()
                if stripped != "":
                    normalized.append(stripped)
            return tuple(normalized)
        return value


@dataclass(slots=True)
class RegistryHTTPPublisher:
    platform_registry: PlatformRegistry
    client: httpx.AsyncClient | None = None
    timeout_seconds: float = 10.0
    connector_name: str = "registry_http"

    async def publish(
        self,
        command: PlatformPublishCommand,
    ) -> PlatformPublishResult:
        config = self._config_for(command)
        response = await self._send(command=command, config=config)
        payload = response_json_object(response)
        self._raise_for_response_error(
            platform=command.platform,
            response=response,
            payload=payload,
            config=config,
        )
        platform_ref = _platform_ref(
            payload=payload,
            response=response,
            config=config,
        )
        if platform_ref is None:
            raise PlatformPublicationError(
                "HTTP-интеграция площадки вернула ответ без platform ref",
                platform=command.platform,
                error_code="invalid_response",
                retryable=True,
            )

        return PlatformPublishResult(
            platform=command.platform,
            platform_ref=f"{command.target_id}:{platform_ref}",
            connector_name=_connector_name(
                platform=command.platform,
                fallback=self.connector_name,
            ),
            published_at=_published_at(
                _published_at_value(payload=payload, config=config),
                fallback=command.requested_at,
            ),
        )

    def _config_for(self, command: PlatformPublishCommand) -> RegistryHTTPConfig:
        try:
            entry = self.platform_registry.require_platform(
                tenant_id=command.tenant_id,
                platform=command.platform,
            )
        except PlatformNotRegisteredError as error:
            raise PlatformPublicationError(
                "Площадка не зарегистрирована в реестре tenant",
                platform=command.platform,
                error_code="platform_not_registered",
                retryable=False,
            ) from error

        if entry.status != PlatformStatus.ACTIVE:
            error_code = (
                "platform_paused"
                if entry.status == PlatformStatus.PAUSED
                else "platform_disabled"
            )
            raise PlatformPublicationError(
                "Площадка недоступна для HTTP-публикации по статусу реестра",
                platform=command.platform,
                error_code=error_code,
                retryable=False,
            )

        raw_config = entry.parameters.get("http")
        if not isinstance(raw_config, dict):
            raise PlatformPublicationError(
                "В реестре площадки не задан parameters.http",
                platform=command.platform,
                error_code="platform_http_config_missing",
                retryable=False,
            )

        try:
            return RegistryHTTPConfig.model_validate(raw_config)
        except ValidationError as error:
            raise PlatformPublicationError(
                "В реестре площадки задан некорректный parameters.http",
                platform=command.platform,
                error_code="platform_http_config_invalid",
                retryable=False,
            ) from error

    async def _send(
        self,
        *,
        command: PlatformPublishCommand,
        config: RegistryHTTPConfig,
    ) -> httpx.Response:
        payload = _request_payload(command=command, config=config)
        headers = _request_headers(command=command, config=config)
        params = dict(config.query_params)
        _apply_token_to_request(
            command=command,
            config=config,
            payload=payload,
            params=params,
        )
        try:
            if self.client is not None:
                return await _send_http_request(
                    client=self.client,
                    config=config,
                    headers=headers,
                    params=params,
                    payload=payload,
                )

            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                return await _send_http_request(
                    client=client,
                    config=config,
                    headers=headers,
                    params=params,
                    payload=payload,
                )
        except httpx.TimeoutException:
            raise PlatformPublicationError(
                "HTTP-интеграция площадки не ответила до таймаута",
                platform=command.platform,
                error_code="platform_timeout",
                retryable=True,
            ) from None
        except httpx.TransportError:
            raise PlatformPublicationError(
                "HTTP-интеграция площадки временно недоступна",
                platform=command.platform,
                error_code="platform_unavailable",
                retryable=True,
            ) from None

    def _raise_for_response_error(
        self,
        *,
        platform: str,
        response: httpx.Response,
        payload: dict[str, Any],
        config: RegistryHTTPConfig,
    ) -> None:
        error_code = _response_error_code(payload)
        if response.is_success and error_code is None:
            return

        retry_after = retry_after_seconds(response.headers.get("Retry-After"))
        normalized_error_code = error_code or str(response.status_code)
        if response.status_code == 429 or _code_in(
            normalized_error_code,
            config.rate_limit_error_codes,
        ):
            raise PlatformPublicationError(
                "HTTP-интеграция площадки вернула rate limit",
                platform=platform,
                error_code="rate_limited",
                retryable=True,
                retry_after_seconds=retry_after,
            )
        if response.status_code == 401 or _code_in(
            normalized_error_code,
            config.auth_error_codes,
        ):
            raise PlatformPublicationError(
                "HTTP-интеграция площадки отклонила токен",
                platform=platform,
                error_code="auth_failed",
                retryable=False,
            )
        if response.status_code == 403 or _code_in(
            normalized_error_code,
            config.access_denied_error_codes,
        ):
            raise PlatformPublicationError(
                "HTTP-интеграция площадки запретила публикацию в цель",
                platform=platform,
                error_code="access_denied",
                retryable=False,
            )
        if response.status_code >= 500 or _code_in(
            normalized_error_code,
            config.retryable_error_codes,
        ):
            raise PlatformPublicationError(
                "HTTP-интеграция площадки временно недоступна",
                platform=platform,
                error_code=(
                    normalized_error_code
                    if response.status_code < 500
                    else "platform_unavailable"
                ),
                retryable=True,
                retry_after_seconds=retry_after,
            )
        if not response.is_success:
            raise PlatformPublicationError(
                "HTTP-интеграция площадки отклонила запрос публикации",
                platform=platform,
                error_code="invalid_request",
                retryable=False,
            )

        raise PlatformPublicationError(
            "HTTP-интеграция площадки вернула ошибку публикации",
            platform=platform,
            error_code=normalized_error_code or "publication_failed",
            retryable=False,
        )


def _request_payload(
    *,
    command: PlatformPublishCommand,
    config: RegistryHTTPConfig,
) -> dict[str, Any]:
    payload: dict[str, Any] = dict(config.extra_payload)
    payload[config.target_field] = command.target_id
    payload[config.content_field] = command.content
    if config.publication_id_field is not None:
        payload[config.publication_id_field] = command.publication_id
    if config.correlation_id_field is not None:
        payload[config.correlation_id_field] = command.correlation_id
    if config.metadata_field is not None:
        payload[config.metadata_field] = clone_json_object(command.metadata)

    media_items = media_items_from_metadata(
        command.metadata,
        platform=command.platform,
    )
    if config.media_field is not None and media_items:
        payload[config.media_field] = [dict(item) for item in media_items]

    platform_metadata = command.metadata.get(command.platform)
    if isinstance(platform_metadata, dict):
        payload.update(cast(dict[str, Any], platform_metadata))

    return payload


def _request_headers(
    *,
    command: PlatformPublishCommand,
    config: RegistryHTTPConfig,
) -> dict[str, str]:
    headers = dict(config.headers)
    token = command.access_token.get_secret_value()
    if config.idempotency_header is not None:
        headers[config.idempotency_header] = command.publication_id
    if config.auth_mode == "bearer":
        headers[config.auth_header] = f"{config.auth_scheme or 'Bearer'} {token}"
    elif config.auth_mode == "oauth":
        headers[config.auth_header] = f"{config.auth_scheme or 'OAuth'} {token}"
    elif config.auth_mode == "header":
        headers[config.auth_header] = token
    return headers


def _apply_token_to_request(
    *,
    command: PlatformPublishCommand,
    config: RegistryHTTPConfig,
    payload: dict[str, Any],
    params: dict[str, str],
) -> None:
    token = command.access_token.get_secret_value()
    if config.auth_mode == "query":
        params[config.token_field] = token
    elif config.auth_mode in {"form", "json"}:
        payload[config.token_field] = token


async def _send_http_request(
    *,
    client: httpx.AsyncClient,
    config: RegistryHTTPConfig,
    headers: dict[str, str],
    params: dict[str, str],
    payload: dict[str, Any],
) -> httpx.Response:
    if config.payload_format == "json":
        return await client.request(
            config.method,
            config.endpoint_url,
            headers=headers,
            params=params,
            json=payload,
        )
    return await client.request(
        config.method,
        config.endpoint_url,
        headers=headers,
        params=params,
        data=payload,
    )


def _platform_ref(
    *,
    payload: dict[str, Any],
    response: httpx.Response,
    config: RegistryHTTPConfig,
) -> str | None:
    for field_name in config.response_ref_fields:
        if field_name == "$text":
            text_ref = response.text.strip()
            if text_ref != "":
                return text_ref
            continue

        value = _lookup_response_value(payload, field_name)
        normalized = _string_ref(value)
        if normalized is not None:
            return normalized
    return None


def _published_at_value(
    *,
    payload: dict[str, Any],
    config: RegistryHTTPConfig,
) -> object:
    for field_name in config.published_at_fields:
        value = _lookup_response_value(payload, field_name)
        if value is not None:
            return value
    return None


def _lookup_response_value(payload: dict[str, Any], field_name: str) -> object:
    dotted_value = _lookup_dotted(payload, field_name)
    if dotted_value is not None:
        return dotted_value

    for container in _response_containers(payload):
        value = _lookup_dotted(container, field_name)
        if value is not None:
            return value
    return None


def _lookup_dotted(container: dict[str, Any], field_name: str) -> object:
    current: object = container
    for part in field_name.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def _response_containers(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    containers: list[dict[str, Any]] = [payload]
    for field_name in ("result", "data", "publication", "post", "article"):
        value = payload.get(field_name)
        if isinstance(value, dict):
            containers.append(cast(dict[str, Any], value))
    return tuple(containers)


def _response_error_code(payload: dict[str, Any]) -> str | None:
    for field_name in ("error_code", "code"):
        normalized = _string_ref(payload.get(field_name))
        if normalized is not None:
            return normalized.lower()

    error = payload.get("error")
    if isinstance(error, dict):
        for field_name in ("error_code", "code"):
            normalized = _string_ref(error.get(field_name))
            if normalized is not None:
                return normalized.lower()
    if isinstance(error, str) and error.strip() != "":
        return error.strip().lower()

    return None


def _string_ref(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float | str):
        normalized = str(value).strip()
        if normalized != "":
            return normalized
    return None


def _published_at(value: object, *, fallback: datetime) -> datetime:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return fallback
    return fallback


def _code_in(error_code: str, candidates: tuple[str, ...]) -> bool:
    normalized = error_code.strip().lower()
    return normalized in {candidate.strip().lower() for candidate in candidates}


def _connector_name(*, platform: str, fallback: str) -> str:
    candidate = f"{platform.strip().lower()}_{fallback}"
    normalized = "".join(
        character if character.isalnum() or character == "_" else "_"
        for character in candidate
    )
    if normalized == "" or not normalized[0].isalpha():
        return fallback
    return normalized[:64]


__all__ = [
    "RegistryHTTPConfig",
    "RegistryHTTPPublisher",
]
