from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from messenger_adapter.base_adapter import (
    PlatformPublicationError,
    PlatformPublishCommand,
    PlatformPublishResult,
)
from messenger_adapter.platform_http import (
    int_or_none,
    response_json_object,
    retry_after_seconds,
)

VK_PLATFORM = "vk"
VK_WALL_POST_METHOD = "wall.post"
VK_API_VERSION = "5.199"

VK_RATE_LIMIT_CODES = {6, 9}
VK_RETRYABLE_CODES = {1, 10}
VK_AUTH_CODES = {5}
VK_ACCESS_DENIED_CODES = {7, 15, 214}
VK_INVALID_REQUEST_CODES = {100}


@dataclass(slots=True)
class VKWallPublisher:
    client: httpx.AsyncClient | None = None
    api_base_url: str = "https://api.vk.com/method"
    api_version: str = VK_API_VERSION
    timeout_seconds: float = 10.0
    connector_name: str = "vk_wall_api"
    from_group: bool = True

    async def publish(
        self,
        command: PlatformPublishCommand,
    ) -> PlatformPublishResult:
        if command.platform != VK_PLATFORM:
            raise PlatformPublicationError(
                "VK adapter получил запрос другой площадки",
                platform=command.platform,
                error_code="platform_mismatch",
                retryable=False,
            )

        response = await self._wall_post(command)
        payload = response_json_object(response)
        self._raise_for_response_error(response=response, payload=payload)

        result = payload.get("response")
        if not isinstance(result, dict):
            raise PlatformPublicationError(
                "VK API вернул ответ без response",
                platform=VK_PLATFORM,
                error_code="invalid_response",
                retryable=True,
            )

        post_id = result.get("post_id")
        if not isinstance(post_id, int | str):
            raise PlatformPublicationError(
                "VK API вернул ответ без post_id",
                platform=VK_PLATFORM,
                error_code="invalid_response",
                retryable=True,
            )

        return PlatformPublishResult(
            platform=VK_PLATFORM,
            platform_ref=f"{command.target_id}:{post_id}",
            connector_name=self.connector_name,
            published_at=_vk_published_at(result.get("date"), command.requested_at),
        )

    async def _wall_post(self, command: PlatformPublishCommand) -> httpx.Response:
        url = f"{self.api_base_url.rstrip('/')}/{VK_WALL_POST_METHOD}"
        payload = self._request_payload(command)
        try:
            if self.client is not None:
                return await self.client.post(url, data=payload)

            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                return await client.post(url, data=payload)
        except httpx.TimeoutException:
            raise PlatformPublicationError(
                "VK API не ответил до таймаута",
                platform=VK_PLATFORM,
                error_code="platform_timeout",
                retryable=True,
            ) from None
        except httpx.TransportError:
            raise PlatformPublicationError(
                "VK API временно недоступен",
                platform=VK_PLATFORM,
                error_code="platform_unavailable",
                retryable=True,
            ) from None

    def _request_payload(self, command: PlatformPublishCommand) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "owner_id": command.target_id,
            "message": command.content,
            "access_token": command.access_token.get_secret_value(),
            "v": self.api_version,
        }
        if self.from_group:
            payload["from_group"] = "1"

        metadata = command.metadata.get(VK_PLATFORM)
        if isinstance(metadata, dict):
            for field_name in (
                "attachments",
                "friends_only",
                "from_group",
                "signed",
                "publish_date",
                "lat",
                "long",
                "place_id",
                "guid",
                "mark_as_ads",
            ):
                if field_name in metadata:
                    payload[field_name] = metadata[field_name]

        return payload

    def _raise_for_response_error(
        self,
        *,
        response: httpx.Response,
        payload: dict[str, Any],
    ) -> None:
        if response.is_success and "error" not in payload:
            return

        if response.status_code == 429:
            raise PlatformPublicationError(
                "VK API вернул rate limit",
                platform=VK_PLATFORM,
                error_code="rate_limited",
                retryable=True,
                retry_after_seconds=retry_after_seconds(
                    response.headers.get("Retry-After")
                ),
            )
        if response.status_code >= 500:
            raise PlatformPublicationError(
                "VK API временно недоступен",
                platform=VK_PLATFORM,
                error_code="platform_unavailable",
                retryable=True,
            )
        if not response.is_success:
            raise PlatformPublicationError(
                "VK API отклонил запрос публикации",
                platform=VK_PLATFORM,
                error_code="invalid_request",
                retryable=False,
            )

        error = payload.get("error")
        if isinstance(error, dict):
            _raise_for_vk_api_error(error=error, response=response)

        raise PlatformPublicationError(
            "VK API вернул ошибку публикации",
            platform=VK_PLATFORM,
            error_code="publication_failed",
            retryable=False,
        )


def _raise_for_vk_api_error(
    *,
    error: dict[str, Any],
    response: httpx.Response,
) -> None:
    error_code = int_or_none(error.get("error_code"))
    retry_after = retry_after_seconds(response.headers.get("Retry-After"))

    if error_code in VK_RATE_LIMIT_CODES:
        raise PlatformPublicationError(
            "VK API вернул rate limit",
            platform=VK_PLATFORM,
            error_code="rate_limited",
            retryable=True,
            retry_after_seconds=retry_after,
        )
    if error_code in VK_RETRYABLE_CODES:
        raise PlatformPublicationError(
            "VK API временно недоступен",
            platform=VK_PLATFORM,
            error_code="platform_unavailable",
            retryable=True,
            retry_after_seconds=retry_after,
        )
    if error_code in VK_AUTH_CODES:
        raise PlatformPublicationError(
            "VK API отклонил токен",
            platform=VK_PLATFORM,
            error_code="auth_failed",
            retryable=False,
        )
    if error_code in VK_ACCESS_DENIED_CODES:
        raise PlatformPublicationError(
            "VK API запретил публикацию в цель",
            platform=VK_PLATFORM,
            error_code="access_denied",
            retryable=False,
        )
    if error_code in VK_INVALID_REQUEST_CODES:
        raise PlatformPublicationError(
            "VK API отклонил параметры публикации",
            platform=VK_PLATFORM,
            error_code="invalid_request",
            retryable=False,
        )

    raise PlatformPublicationError(
        "VK API вернул ошибку публикации",
        platform=VK_PLATFORM,
        error_code="publication_failed",
        retryable=False,
    )


def _vk_published_at(value: object, fallback: datetime) -> datetime:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=fallback.tzinfo or UTC)
    return fallback
