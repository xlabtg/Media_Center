from __future__ import annotations

import asyncio
import hashlib
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import ConfigDict, Field, SecretStr, field_validator

from libs.shared.models import CorrelationId, JSONValue, SharedBaseModel, TenantId
from messenger_adapter.base_adapter import (
    PlatformPublicationError,
    PlatformPublishCommand,
    PlatformPublishResult,
    PlatformTokenRepository,
)
from messenger_adapter.platform_http import (
    int_or_none,
    response_json_object,
    retry_after_seconds,
)

VK_PLATFORM = "vk"
VK_WALL_POST_METHOD = "wall.post"
VK_WALL_GET_BY_ID_METHOD = "wall.getById"
VK_STATS_GET_POST_REACH_METHOD = "stats.getPostReach"
VK_API_VERSION = "5.199"
VK_METRICS_CONNECTOR_NAME = "vk_post_metrics_api"

VK_RATE_LIMIT_CODES = {6, 9, 29, 223}
VK_RETRYABLE_CODES = {1, 10}
VK_AUTH_CODES = {5, 16, 17, 27, 28, 1114, 1116, 1117, 1118}
VK_ACCESS_DENIED_CODES = {7, 15, 200, 203, 210, 214}
VK_INVALID_REQUEST_CODES = {100, 219, 220, 222, 224, 225}

_SHA256_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"

VKDelaySleeper = Callable[[float], Awaitable[None] | None]


class VKAPIRateLimit(SharedBaseModel):
    """Local VK API pacing applied before outbound method calls."""

    model_config = ConfigDict(frozen=True)

    max_requests_per_second: int = Field(default=3, ge=1, le=1000)
    min_interval_seconds: float = Field(default=0.34, ge=0, le=3600)

    @property
    def effective_interval_seconds(self) -> float:
        return max(self.min_interval_seconds, 1 / self.max_requests_per_second)


@dataclass(slots=True)
class VKAPIRateLimiter:
    limit: VKAPIRateLimit = field(default_factory=VKAPIRateLimit)
    sleeper: VKDelaySleeper = field(default_factory=lambda: _default_sleep)
    _last_called_at: dict[tuple[str, str, str], datetime] = field(
        default_factory=dict,
        init=False,
    )

    async def acquire(
        self,
        *,
        tenant_id: str,
        target_ref: str,
        action: str,
        now: datetime | str | None = None,
    ) -> float:
        requested_at = _normalize_datetime(now or datetime.now(UTC))
        key = (tenant_id, target_ref, action)
        last_called_at = self._last_called_at.get(key)
        delay = 0.0
        if last_called_at is not None:
            elapsed = (requested_at - last_called_at).total_seconds()
            delay = max(0.0, self.limit.effective_interval_seconds - elapsed)

        if delay > 0:
            await _sleep(self.sleeper, delay)

        self._last_called_at[key] = requested_at + timedelta(seconds=delay)
        return delay


class VKPostMetricsRequest(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    tenant_id: TenantId
    target_id: str = Field(min_length=1, max_length=256)
    post_ids: tuple[int, ...] = Field(min_length=1, max_length=30)
    correlation_id: CorrelationId
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("post_ids")
    @classmethod
    def _validate_post_ids(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(post_id < 1 for post_id in value):
            raise ValueError("post_ids должны быть положительными")
        return value


class VKPostMetrics(SharedBaseModel):
    tenant_id: TenantId
    platform: str = VK_PLATFORM
    post_id: int = Field(ge=1)
    platform_ref_hash: str = Field(pattern=_SHA256_HASH_PATTERN)
    collected_at: datetime
    reach_total: int = Field(default=0, ge=0)
    reach_subscribers: int = Field(default=0, ge=0)
    reach_viral: int = Field(default=0, ge=0)
    reach_ads: int = Field(default=0, ge=0)
    views: int = Field(default=0, ge=0)
    likes: int = Field(default=0, ge=0)
    comments: int = Field(default=0, ge=0)
    reposts: int = Field(default=0, ge=0)
    links: int = Field(default=0, ge=0)
    to_group: int = Field(default=0, ge=0)
    join_group: int = Field(default=0, ge=0)
    hide: int = Field(default=0, ge=0)
    report: int = Field(default=0, ge=0)
    unsubscribe: int = Field(default=0, ge=0)
    engagement_count: int = Field(default=0, ge=0)

    @field_validator("collected_at")
    @classmethod
    def _normalize_collected_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class VKPostMetricsFailure(SharedBaseModel):
    post_id: int = Field(ge=1)
    error_code: str = Field(min_length=1, max_length=128)
    retryable: bool


class VKPostMetricsBatch(SharedBaseModel):
    tenant_id: TenantId
    target_ref_hash: str = Field(pattern=_SHA256_HASH_PATTERN)
    collected_at: datetime
    correlation_id: CorrelationId
    connector_name: str = VK_METRICS_CONNECTOR_NAME
    metrics: tuple[VKPostMetrics, ...] = Field(default_factory=tuple)
    failed: tuple[VKPostMetricsFailure, ...] = Field(default_factory=tuple)

    @field_validator("collected_at")
    @classmethod
    def _normalize_collected_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


@dataclass(slots=True)
class VKPostMetricsCollector:
    token_store: PlatformTokenRepository
    client: httpx.AsyncClient | None = None
    api_base_url: str = "https://api.vk.com/method"
    api_version: str = VK_API_VERSION
    timeout_seconds: float = 10.0
    connector_name: str = VK_METRICS_CONNECTOR_NAME
    rate_limiter: VKAPIRateLimiter = field(default_factory=VKAPIRateLimiter)

    async def collect(
        self,
        request: VKPostMetricsRequest,
        *,
        token_id: str | None = None,
        now: datetime | str | None = None,
    ) -> VKPostMetricsBatch:
        collected_at = _normalize_datetime(now or datetime.now(UTC))
        token_record = self.token_store.require_token(
            tenant_id=request.tenant_id,
            platform=VK_PLATFORM,
            token_id=token_id,
        )
        access_token = self.token_store.decrypt_token(
            token_record,
            tenant_id=request.tenant_id,
        )

        try:
            reach_payload = await self._api_call(
                method=VK_STATS_GET_POST_REACH_METHOD,
                payload={
                    "owner_id": request.target_id,
                    "post_ids": ",".join(str(post_id) for post_id in request.post_ids),
                },
                access_token=access_token,
                tenant_id=request.tenant_id,
                target_ref=request.target_id,
            )
            wall_payload = await self._api_call(
                method=VK_WALL_GET_BY_ID_METHOD,
                payload={
                    "posts": ",".join(
                        f"{request.target_id}_{post_id}" for post_id in request.post_ids
                    ),
                },
                access_token=access_token,
                tenant_id=request.tenant_id,
                target_ref=request.target_id,
            )
        except Exception as error:
            publication_error = _as_vk_publication_error(error)
            return VKPostMetricsBatch(
                tenant_id=request.tenant_id,
                target_ref_hash=_scoped_hash(
                    tenant_id=request.tenant_id,
                    value=f"{VK_PLATFORM}:{request.target_id}",
                ),
                collected_at=collected_at,
                correlation_id=request.correlation_id,
                connector_name=self.connector_name,
                failed=tuple(
                    VKPostMetricsFailure(
                        post_id=post_id,
                        error_code=publication_error.error_code,
                        retryable=publication_error.retryable,
                    )
                    for post_id in request.post_ids
                ),
            )

        return VKPostMetricsBatch(
            tenant_id=request.tenant_id,
            target_ref_hash=_scoped_hash(
                tenant_id=request.tenant_id,
                value=f"{VK_PLATFORM}:{request.target_id}",
            ),
            collected_at=collected_at,
            correlation_id=request.correlation_id,
            connector_name=self.connector_name,
            metrics=tuple(
                _vk_post_metrics(
                    tenant_id=request.tenant_id,
                    target_id=request.target_id,
                    post_id=post_id,
                    reach=_reach_by_post_id(reach_payload).get(post_id, {}),
                    wall_post=_wall_posts_by_post_id(wall_payload).get(post_id, {}),
                    collected_at=collected_at,
                )
                for post_id in request.post_ids
            ),
        )

    async def _api_call(
        self,
        *,
        method: str,
        payload: dict[str, Any],
        access_token: SecretStr,
        tenant_id: str,
        target_ref: str,
    ) -> dict[str, Any]:
        await self.rate_limiter.acquire(
            tenant_id=tenant_id,
            target_ref=target_ref,
            action=method,
        )
        url = f"{self.api_base_url.rstrip('/')}/{method}"
        form_payload = dict(payload)
        form_payload["access_token"] = access_token.get_secret_value()
        form_payload["v"] = self.api_version
        try:
            if self.client is not None:
                response = await self.client.post(url, data=form_payload)
            else:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.post(url, data=form_payload)
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

        response_payload = response_json_object(response)
        _raise_for_vk_response_error(response=response, payload=response_payload)
        return response_payload


@dataclass(slots=True)
class VKWallPublisher:
    client: httpx.AsyncClient | None = None
    api_base_url: str = "https://api.vk.com/method"
    api_version: str = VK_API_VERSION
    timeout_seconds: float = 10.0
    connector_name: str = "vk_wall_api"
    from_group: bool = True
    rate_limiter: VKAPIRateLimiter = field(default_factory=VKAPIRateLimiter)

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
        await self.rate_limiter.acquire(
            tenant_id=command.tenant_id,
            target_ref=command.target_id,
            action=VK_WALL_POST_METHOD,
        )
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
                    payload[field_name] = _vk_form_value(metadata[field_name])

        return payload

    def _raise_for_response_error(
        self,
        *,
        response: httpx.Response,
        payload: dict[str, Any],
    ) -> None:
        _raise_for_vk_response_error(response=response, payload=payload)


async def _default_sleep(delay_seconds: float) -> None:
    await asyncio.sleep(delay_seconds)


async def _sleep(sleeper: VKDelaySleeper, delay_seconds: float) -> None:
    sleep_result = sleeper(delay_seconds)
    if inspect.isawaitable(sleep_result):
        await sleep_result


def _vk_form_value(value: object) -> object:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, list | tuple):
        return ",".join(str(_vk_form_value(item)) for item in value)
    return value


def _raise_for_vk_response_error(
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
            "VK API отклонил запрос",
            platform=VK_PLATFORM,
            error_code="invalid_request",
            retryable=False,
        )

    error = payload.get("error")
    if isinstance(error, dict):
        _raise_for_vk_api_error(error=error, response=response)

    raise PlatformPublicationError(
        "VK API вернул ошибку",
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


def _as_vk_publication_error(error: Exception) -> PlatformPublicationError:
    if isinstance(error, PlatformPublicationError):
        return error
    return PlatformPublicationError(
        "Сбой сбора метрик VK API",
        platform=VK_PLATFORM,
        error_code="metrics_collection_failed",
        retryable=True,
    )


def _reach_by_post_id(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    response = payload.get("response")
    if not isinstance(response, list):
        return {}

    result: dict[int, dict[str, Any]] = {}
    for item in response:
        if not isinstance(item, dict):
            continue
        post_id = int_or_none(item.get("post_id"))
        if post_id is not None and post_id > 0:
            result[post_id] = item
    return result


def _wall_posts_by_post_id(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    response = payload.get("response")
    items = response.get("items") if isinstance(response, dict) else response
    if not isinstance(items, list):
        return {}

    result: dict[int, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        post_id = int_or_none(item.get("id"))
        if post_id is None:
            post_id = int_or_none(item.get("post_id"))
        if post_id is not None and post_id > 0:
            result[post_id] = item
    return result


def _vk_post_metrics(
    *,
    tenant_id: str,
    target_id: str,
    post_id: int,
    reach: dict[str, Any],
    wall_post: dict[str, Any],
    collected_at: datetime,
) -> VKPostMetrics:
    links = _nonnegative_int(reach.get("links"))
    likes = _counter_value(wall_post.get("likes"))
    comments = _counter_value(wall_post.get("comments"))
    reposts = _counter_value(wall_post.get("reposts"))
    return VKPostMetrics(
        tenant_id=tenant_id,
        post_id=post_id,
        platform_ref_hash=_scoped_hash(
            tenant_id=tenant_id,
            value=f"{VK_PLATFORM}:{target_id}:{post_id}",
        ),
        collected_at=collected_at,
        reach_total=_coalesced_count(reach, "reach_total", "reach_total_count"),
        reach_subscribers=_coalesced_count(
            reach,
            "reach_subscribers",
            "reach_subscribers_count",
        ),
        reach_viral=_nonnegative_int(reach.get("reach_viral")),
        reach_ads=_nonnegative_int(reach.get("reach_ads")),
        views=_counter_value(wall_post.get("views")),
        likes=likes,
        comments=comments,
        reposts=reposts,
        links=links,
        to_group=_nonnegative_int(reach.get("to_group")),
        join_group=_nonnegative_int(reach.get("join_group")),
        hide=_nonnegative_int(reach.get("hide")),
        report=_nonnegative_int(reach.get("report")),
        unsubscribe=_nonnegative_int(reach.get("unsubscribe")),
        engagement_count=likes + comments + reposts + links,
    )


def _counter_value(value: object) -> int:
    if isinstance(value, dict):
        return _nonnegative_int(value.get("count"))
    return _nonnegative_int(value)


def _coalesced_count(payload: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = _nonnegative_int(payload.get(key))
        if value > 0:
            return value
    return 0


def _nonnegative_int(value: object) -> int:
    resolved = int_or_none(value)
    if resolved is None or resolved < 0:
        return 0
    return resolved


def _scoped_hash(*, tenant_id: str, value: str) -> str:
    return "sha256:" + hashlib.sha256(f"{tenant_id}:{value}".encode()).hexdigest()


def _normalize_datetime(value: datetime | str) -> datetime:
    if isinstance(value, str):
        normalized = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        normalized = value

    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=UTC)

    return normalized.astimezone(UTC)


def _vk_published_at(value: object, fallback: datetime) -> datetime:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=fallback.tzinfo or UTC)
    return fallback
