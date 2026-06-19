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

TELEGRAM_PLATFORM = "telegram"
TELEGRAM_SEND_MESSAGE_METHOD = "sendMessage"


@dataclass(slots=True)
class TelegramBotApiPublisher:
    client: httpx.AsyncClient | None = None
    api_base_url: str = "https://api.telegram.org"
    timeout_seconds: float = 10.0
    connector_name: str = "telegram_bot_api"
    parse_mode: str | None = None
    disable_notification: bool = False

    async def publish(
        self,
        command: PlatformPublishCommand,
    ) -> PlatformPublishResult:
        if command.platform != TELEGRAM_PLATFORM:
            raise PlatformPublicationError(
                "Telegram adapter получил запрос другой площадки",
                platform=command.platform,
                error_code="platform_mismatch",
                retryable=False,
            )

        response = await self._send_message(command)
        payload = response_json_object(response)
        self._raise_for_response_error(response=response, payload=payload)

        result = payload.get("result")
        if not isinstance(result, dict):
            raise PlatformPublicationError(
                "Telegram API вернул ответ без result",
                platform=TELEGRAM_PLATFORM,
                error_code="invalid_response",
                retryable=True,
            )

        message_id = result.get("message_id")
        if not isinstance(message_id, int | str):
            raise PlatformPublicationError(
                "Telegram API вернул ответ без message_id",
                platform=TELEGRAM_PLATFORM,
                error_code="invalid_response",
                retryable=True,
            )

        chat_ref = _telegram_chat_ref(result, command.target_id)
        return PlatformPublishResult(
            platform=TELEGRAM_PLATFORM,
            platform_ref=f"{chat_ref}:{message_id}",
            connector_name=self.connector_name,
            published_at=_telegram_published_at(
                result.get("date"),
                fallback=command.requested_at,
            ),
        )

    async def _send_message(self, command: PlatformPublishCommand) -> httpx.Response:
        token = command.access_token.get_secret_value()
        url = (
            f"{self.api_base_url.rstrip('/')}/bot{token}/{TELEGRAM_SEND_MESSAGE_METHOD}"
        )
        payload = self._request_payload(command)
        try:
            if self.client is not None:
                return await self.client.post(url, json=payload)

            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                return await client.post(url, json=payload)
        except httpx.TimeoutException:
            raise PlatformPublicationError(
                "Telegram API не ответил до таймаута",
                platform=TELEGRAM_PLATFORM,
                error_code="platform_timeout",
                retryable=True,
            ) from None
        except httpx.TransportError:
            raise PlatformPublicationError(
                "Telegram API временно недоступен",
                platform=TELEGRAM_PLATFORM,
                error_code="platform_unavailable",
                retryable=True,
            ) from None

    def _request_payload(self, command: PlatformPublishCommand) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": command.target_id,
            "text": command.content,
        }
        if self.parse_mode is not None:
            payload["parse_mode"] = self.parse_mode
        if self.disable_notification:
            payload["disable_notification"] = True

        metadata = command.metadata.get(TELEGRAM_PLATFORM)
        if isinstance(metadata, dict):
            for field_name in (
                "message_thread_id",
                "parse_mode",
                "disable_notification",
                "protect_content",
                "reply_to_message_id",
                "link_preview_options",
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
        if response.is_success and payload.get("ok") is True:
            return

        api_error_code = int_or_none(payload.get("error_code"))
        retry_after = _telegram_retry_after(response=response, payload=payload)
        status_code = response.status_code

        if status_code == 429 or api_error_code == 429:
            raise PlatformPublicationError(
                "Telegram API вернул rate limit",
                platform=TELEGRAM_PLATFORM,
                error_code="rate_limited",
                retryable=True,
                retry_after_seconds=retry_after,
            )
        if status_code == 401 or api_error_code == 401:
            raise PlatformPublicationError(
                "Telegram API отклонил токен",
                platform=TELEGRAM_PLATFORM,
                error_code="auth_failed",
                retryable=False,
            )
        if status_code == 403 or api_error_code == 403:
            raise PlatformPublicationError(
                "Telegram API запретил публикацию в цель",
                platform=TELEGRAM_PLATFORM,
                error_code="access_denied",
                retryable=False,
            )
        if status_code >= 500:
            raise PlatformPublicationError(
                "Telegram API временно недоступен",
                platform=TELEGRAM_PLATFORM,
                error_code="platform_unavailable",
                retryable=True,
            )
        if not response.is_success:
            raise PlatformPublicationError(
                "Telegram API отклонил запрос публикации",
                platform=TELEGRAM_PLATFORM,
                error_code="invalid_request",
                retryable=False,
            )

        raise PlatformPublicationError(
            "Telegram API вернул ошибку публикации",
            platform=TELEGRAM_PLATFORM,
            error_code="publication_failed",
            retryable=False,
        )


def _telegram_retry_after(
    *,
    response: httpx.Response,
    payload: dict[str, Any],
) -> float | None:
    parameters = payload.get("parameters")
    if isinstance(parameters, dict):
        retry_after = retry_after_seconds(parameters.get("retry_after"))
        if retry_after is not None:
            return retry_after

    return retry_after_seconds(response.headers.get("Retry-After"))


def _telegram_chat_ref(result: dict[str, Any], fallback: str) -> str:
    chat = result.get("chat")
    if isinstance(chat, dict):
        chat_id = chat.get("id")
        if isinstance(chat_id, int | str):
            return str(chat_id)

    return fallback


def _telegram_published_at(value: object, *, fallback: datetime) -> datetime:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=UTC)
    return fallback
