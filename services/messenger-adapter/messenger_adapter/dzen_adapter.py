from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import httpx

from libs.shared.models import JSONValue
from messenger_adapter.base_adapter import (
    PlatformPublicationError,
    PlatformPublishCommand,
    PlatformPublishResult,
)
from messenger_adapter.content_transformer import media_items_from_metadata
from messenger_adapter.platform_http import (
    int_or_none,
    response_json_object,
    retry_after_seconds,
)

DZEN_PLATFORM = "dzen"


@dataclass(slots=True)
class DzenPostPublisher:
    client: httpx.AsyncClient | None = None
    api_base_url: str = "https://dzen.ru/api/v1"
    timeout_seconds: float = 10.0
    connector_name: str = "dzen_api"
    publication_path: str = "/publications"
    authorization_scheme: str = "OAuth"

    async def publish(
        self,
        command: PlatformPublishCommand,
    ) -> PlatformPublishResult:
        if command.platform != DZEN_PLATFORM:
            raise PlatformPublicationError(
                "Dzen adapter получил запрос другой площадки",
                platform=command.platform,
                error_code="platform_mismatch",
                retryable=False,
            )

        response = await self._create_publication(command)
        payload = response_json_object(response)
        self._raise_for_response_error(response=response, payload=payload)
        publication_id = _dzen_publication_id(payload)
        if publication_id is None:
            raise PlatformPublicationError(
                "Dzen API вернул ответ без publication_id",
                platform=DZEN_PLATFORM,
                error_code="invalid_response",
                retryable=True,
            )

        return PlatformPublishResult(
            platform=DZEN_PLATFORM,
            platform_ref=f"{command.target_id}:{publication_id}",
            connector_name=self.connector_name,
            published_at=_dzen_published_at(
                _dzen_published_at_value(payload),
                fallback=command.requested_at,
            ),
        )

    async def _create_publication(
        self,
        command: PlatformPublishCommand,
    ) -> httpx.Response:
        url = f"{self.api_base_url.rstrip('/')}/{self.publication_path.lstrip('/')}"
        payload = self._request_payload(command)
        token = command.access_token.get_secret_value()
        headers = {
            "Authorization": f"{self.authorization_scheme} {token}",
            "Idempotency-Key": command.publication_id,
        }
        try:
            if self.client is not None:
                return await self.client.post(url, json=payload, headers=headers)

            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                return await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException:
            raise PlatformPublicationError(
                "Dzen API не ответил до таймаута",
                platform=DZEN_PLATFORM,
                error_code="platform_timeout",
                retryable=True,
            ) from None
        except httpx.TransportError:
            raise PlatformPublicationError(
                "Dzen API временно недоступен",
                platform=DZEN_PLATFORM,
                error_code="platform_unavailable",
                retryable=True,
            ) from None

    def _request_payload(self, command: PlatformPublishCommand) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "channel_id": command.target_id,
            "text": command.content,
        }
        dzen_metadata = _platform_metadata(command.metadata)
        for field_name in (
            "title",
            "tags",
            "rubric",
            "publish_at",
            "disable_comments",
            "draft",
        ):
            if field_name in dzen_metadata:
                payload[field_name] = dzen_metadata[field_name]

        media_items = media_items_from_metadata(
            command.metadata,
            platform=DZEN_PLATFORM,
        )
        if media_items:
            payload["media"] = [dict(item) for item in media_items]

        return payload

    def _raise_for_response_error(
        self,
        *,
        response: httpx.Response,
        payload: dict[str, Any],
    ) -> None:
        error_code = _dzen_error_code(payload)
        if response.is_success and error_code is None:
            return

        retry_after = retry_after_seconds(response.headers.get("Retry-After"))
        status_code = response.status_code
        if status_code == 429 or error_code in {"rate_limited", "too_many_requests"}:
            raise PlatformPublicationError(
                "Dzen API вернул rate limit",
                platform=DZEN_PLATFORM,
                error_code="rate_limited",
                retryable=True,
                retry_after_seconds=retry_after,
            )
        if status_code == 401 or error_code in {"auth_failed", "unauthorized"}:
            raise PlatformPublicationError(
                "Dzen API отклонил токен",
                platform=DZEN_PLATFORM,
                error_code="auth_failed",
                retryable=False,
            )
        if status_code == 403 or error_code in {"access_denied", "forbidden"}:
            raise PlatformPublicationError(
                "Dzen API запретил публикацию в цель",
                platform=DZEN_PLATFORM,
                error_code="access_denied",
                retryable=False,
            )
        if status_code >= 500:
            raise PlatformPublicationError(
                "Dzen API временно недоступен",
                platform=DZEN_PLATFORM,
                error_code="platform_unavailable",
                retryable=True,
                retry_after_seconds=retry_after,
            )
        if not response.is_success:
            raise PlatformPublicationError(
                "Dzen API отклонил запрос публикации",
                platform=DZEN_PLATFORM,
                error_code="invalid_request",
                retryable=False,
            )

        raise PlatformPublicationError(
            "Dzen API вернул ошибку публикации",
            platform=DZEN_PLATFORM,
            error_code=str(error_code or "publication_failed"),
            retryable=False,
        )


def _platform_metadata(metadata: dict[str, JSONValue]) -> dict[str, JSONValue]:
    value = metadata.get(DZEN_PLATFORM)
    if isinstance(value, dict):
        return value
    return {}


def _dzen_publication_id(payload: dict[str, Any]) -> str | None:
    container = _dzen_result_container(payload)
    for field_name in ("publication_id", "id", "post_id", "article_id"):
        value = container.get(field_name)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | str):
            normalized = str(value).strip()
            if normalized != "":
                return normalized
    return None


def _dzen_published_at_value(payload: dict[str, Any]) -> object:
    container = _dzen_result_container(payload)
    for field_name in ("published_at", "created_at", "date"):
        if field_name in container:
            return container[field_name]
    return None


def _dzen_result_container(payload: dict[str, Any]) -> dict[str, Any]:
    for field_name in ("result", "data", "publication"):
        value = payload.get(field_name)
        if isinstance(value, dict):
            return cast(dict[str, Any], value)
    return payload


def _dzen_error_code(payload: dict[str, Any]) -> str | None:
    for field_name in ("error_code", "code"):
        value = payload.get(field_name)
        if isinstance(value, int | str) and not isinstance(value, bool):
            return str(value).strip().lower()

    error = payload.get("error")
    if isinstance(error, dict):
        for field_name in ("error_code", "code"):
            value = error.get(field_name)
            if isinstance(value, int | str) and not isinstance(value, bool):
                return str(value).strip().lower()
    if isinstance(error, str) and error.strip() != "":
        return error.strip().lower()

    status = int_or_none(payload.get("status"))
    if status is not None and status >= 400:
        return str(status)
    return None


def _dzen_published_at(value: object, *, fallback: datetime) -> datetime:
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
