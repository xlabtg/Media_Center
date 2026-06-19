from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, cast

import httpx
from pydantic import SecretStr

from libs.shared.models import JSONValue
from messenger_adapter.base_adapter import (
    PlatformPublicationError,
    PlatformPublishCommand,
    PlatformPublishResult,
)
from messenger_adapter.content_transformer import media_items_from_metadata
from messenger_adapter.platform_http import int_or_none, retry_after_seconds

OK_PLATFORM = "ok"
OK_MEDIATOPIC_POST_METHOD = "mediatopic.post"

OK_RATE_LIMIT_CODES = {18, 429}
OK_RETRYABLE_CODES = {1, 2, 500}
OK_AUTH_CODES = {101, 102, 103, 104}
OK_ACCESS_DENIED_CODES = {10, 200}


@dataclass(slots=True)
class OKMediatopicPublisher:
    client: httpx.AsyncClient | None = None
    api_base_url: str = "https://api.ok.ru/fb.do"
    timeout_seconds: float = 10.0
    connector_name: str = "ok_mediatopic_api"
    default_topic_type: str = "GROUP_THEME"
    application_key: str | None = None
    application_secret_key: SecretStr | None = None

    async def publish(
        self,
        command: PlatformPublishCommand,
    ) -> PlatformPublishResult:
        if command.platform != OK_PLATFORM:
            raise PlatformPublicationError(
                "OK adapter получил запрос другой площадки",
                platform=command.platform,
                error_code="platform_mismatch",
                retryable=False,
            )

        response = await self._post_mediatopic(command)
        payload = _ok_response_payload(response)
        self._raise_for_response_error(response=response, payload=payload)
        topic_id = _ok_topic_id(payload)
        if topic_id is None:
            raise PlatformPublicationError(
                "OK API вернул ответ без topic id",
                platform=OK_PLATFORM,
                error_code="invalid_response",
                retryable=True,
            )

        return PlatformPublishResult(
            platform=OK_PLATFORM,
            platform_ref=f"{command.target_id}:{topic_id}",
            connector_name=self.connector_name,
            published_at=command.requested_at,
        )

    async def _post_mediatopic(
        self,
        command: PlatformPublishCommand,
    ) -> httpx.Response:
        payload = self._request_payload(command)
        try:
            if self.client is not None:
                return await self.client.post(self.api_base_url, data=payload)

            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                return await client.post(self.api_base_url, data=payload)
        except httpx.TimeoutException:
            raise PlatformPublicationError(
                "OK API не ответил до таймаута",
                platform=OK_PLATFORM,
                error_code="platform_timeout",
                retryable=True,
            ) from None
        except httpx.TransportError:
            raise PlatformPublicationError(
                "OK API временно недоступен",
                platform=OK_PLATFORM,
                error_code="platform_unavailable",
                retryable=True,
            ) from None

    def _request_payload(self, command: PlatformPublishCommand) -> dict[str, str]:
        ok_metadata = _platform_metadata(command.metadata)
        topic_type = (
            _metadata_string(ok_metadata.get("type")) or self.default_topic_type
        )
        target_field = _target_field_for_topic_type(topic_type)
        target_id = _metadata_string(ok_metadata.get(target_field)) or command.target_id
        payload: dict[str, str] = {
            "method": OK_MEDIATOPIC_POST_METHOD,
            "format": "json",
            "access_token": command.access_token.get_secret_value(),
            "attachment": json.dumps(
                _ok_attachment(command),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "type": topic_type,
            target_field: target_id,
        }
        if self.application_key is not None:
            payload["application_key"] = self.application_key

        for field_name in (
            "uid",
            "gid",
            "set_status",
            "location",
            "place_id",
            "comment_as_group",
            "disable_comments",
        ):
            if field_name not in payload and field_name in ok_metadata:
                payload[field_name] = _form_value(ok_metadata[field_name])

        if self.application_secret_key is not None:
            payload["sig"] = _ok_signature(
                payload,
                access_token=command.access_token.get_secret_value(),
                application_secret=self.application_secret_key.get_secret_value(),
            )

        return payload

    def _raise_for_response_error(
        self,
        *,
        response: httpx.Response,
        payload: object,
    ) -> None:
        error_code = _ok_error_code(payload)
        if response.is_success and error_code is None:
            return

        retry_after = retry_after_seconds(response.headers.get("Retry-After"))
        status_code = response.status_code
        if status_code == 429 or error_code in OK_RATE_LIMIT_CODES:
            raise PlatformPublicationError(
                "OK API вернул rate limit",
                platform=OK_PLATFORM,
                error_code="rate_limited",
                retryable=True,
                retry_after_seconds=retry_after,
            )
        if status_code >= 500 or error_code in OK_RETRYABLE_CODES:
            raise PlatformPublicationError(
                "OK API временно недоступен",
                platform=OK_PLATFORM,
                error_code="platform_unavailable",
                retryable=True,
                retry_after_seconds=retry_after,
            )
        if status_code == 401 or error_code in OK_AUTH_CODES:
            raise PlatformPublicationError(
                "OK API отклонил токен",
                platform=OK_PLATFORM,
                error_code="auth_failed",
                retryable=False,
            )
        if status_code == 403 or error_code in OK_ACCESS_DENIED_CODES:
            raise PlatformPublicationError(
                "OK API запретил публикацию в цель",
                platform=OK_PLATFORM,
                error_code="access_denied",
                retryable=False,
            )
        if not response.is_success:
            raise PlatformPublicationError(
                "OK API отклонил запрос публикации",
                platform=OK_PLATFORM,
                error_code="invalid_request",
                retryable=False,
            )

        raise PlatformPublicationError(
            "OK API вернул ошибку публикации",
            platform=OK_PLATFORM,
            error_code=str(error_code or "publication_failed"),
            retryable=False,
        )


def _platform_metadata(metadata: dict[str, JSONValue]) -> dict[str, JSONValue]:
    value = metadata.get(OK_PLATFORM)
    if isinstance(value, dict):
        return value
    return {}


def _ok_attachment(command: PlatformPublishCommand) -> dict[str, JSONValue]:
    media: list[JSONValue] = [{"type": "text", "text": command.content}]
    media.extend(
        dict(item)
        for item in media_items_from_metadata(command.metadata, platform=OK_PLATFORM)
    )
    attachment: dict[str, JSONValue] = {"media": media}
    ok_metadata = _platform_metadata(command.metadata)
    for field_name in ("publishAt", "publishAtMs"):
        if field_name in ok_metadata:
            attachment[field_name] = ok_metadata[field_name]
    return attachment


def _target_field_for_topic_type(topic_type: str) -> str:
    if topic_type.strip().upper().startswith("GROUP"):
        return "gid"
    return "uid"


def _metadata_string(value: JSONValue | None) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float | str):
        normalized = str(value).strip()
        if normalized != "":
            return normalized
    return None


def _form_value(value: JSONValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float | str):
        return str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _ok_signature(
    params: dict[str, str],
    *,
    access_token: str,
    application_secret: str,
) -> str:
    session_secret = hashlib.md5(
        f"{access_token}{application_secret}".encode(),
        usedforsecurity=False,
    ).hexdigest()
    signature_base = "".join(
        f"{key}={params[key]}" for key in sorted(params) if key != "sig"
    )
    return hashlib.md5(
        f"{signature_base}{session_secret}".encode(),
        usedforsecurity=False,
    ).hexdigest()


def _ok_response_payload(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return response.text.strip()


def _ok_topic_id(payload: object) -> str | None:
    if payload is None or isinstance(payload, bool):
        return None
    if isinstance(payload, int | float | str):
        normalized = str(payload).strip()
        return normalized or None
    if not isinstance(payload, dict):
        return None

    data = cast(dict[str, Any], payload)
    for field_name in ("topic_id", "topicId", "id", "result", "response"):
        value = data.get(field_name)
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, int | float | str):
            normalized = str(value).strip()
            if normalized != "":
                return normalized
        if isinstance(value, dict):
            nested = _ok_topic_id(value)
            if nested is not None:
                return nested

    return None


def _ok_error_code(payload: object) -> int | None:
    if not isinstance(payload, dict):
        return None

    data = cast(dict[str, Any], payload)
    direct_code = int_or_none(data.get("error_code"))
    if direct_code is not None:
        return direct_code

    error = data.get("error")
    if isinstance(error, dict):
        return int_or_none(error.get("error_code") or error.get("code"))

    return None
