from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest
from messenger_adapter import (
    BasePlatformAdapter,
    InMemoryPlatformTokenStore,
    PlatformPublicationError,
    PlatformPublishCommand,
    PlatformTokenCipher,
    PublicationRequest,
    RetryPolicy,
    TelegramBotApiPublisher,
    VKWallPublisher,
)
from pydantic import SecretStr

from libs.shared import InMemoryEventBus


def _encryption_key() -> str:
    return base64.b64encode(b"1" * 32).decode("ascii")


def test_telegram_adapter_uses_retry_after_and_redacts_token_in_events() -> None:
    asyncio.run(_run_telegram_rate_limit_scenario())


async def _run_telegram_rate_limit_scenario() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        payload = json.loads(request.content.decode("utf-8"))

        assert request.method == "POST"
        assert request.url.path == "/bottg-secret-token/sendMessage"
        assert payload["chat_id"] == "@nmc_channel"
        assert payload["text"] == "Готовый материал для Telegram"

        if call_count == 1:
            return httpx.Response(
                429,
                json={
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests",
                    "parameters": {"retry_after": 7},
                },
            )

        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "message_id": 42,
                    "date": 1781793600,
                    "chat": {"id": -100123},
                },
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        token_store = InMemoryPlatformTokenStore(PlatformTokenCipher(_encryption_key()))
        token_store.save_token(
            tenant_id="tenant-a",
            platform="telegram",
            token="tg-secret-token",
        )
        bus = InMemoryEventBus()
        recorded_delays: list[float] = []
        adapter = BasePlatformAdapter(
            platform="telegram",
            publisher=TelegramBotApiPublisher(client=client),
            token_store=token_store,
            retry_policy=RetryPolicy(max_attempts=2, initial_delay_seconds=1),
            event_publisher=bus,
            sleeper=recorded_delays.append,
        )

        receipt = await adapter.publish(
            PublicationRequest(
                tenant_id="tenant-a",
                platform="telegram",
                publication_id="pub-telegram-1",
                target_id="@nmc_channel",
                content="Готовый материал для Telegram",
                correlation_id="corr-telegram-1",
            )
        )

    assert call_count == 2
    assert recorded_delays == [7.0]
    assert receipt.platform_ref_hash.startswith("sha256:")
    assert receipt.published_at == datetime.fromtimestamp(1781793600, tz=UTC)
    assert "tg-secret-token" not in bus.messages[-1].envelope.to_json()


def test_vk_adapter_publishes_wall_post_and_redacts_token_in_events() -> None:
    asyncio.run(_run_vk_wall_post_scenario())


async def _run_vk_wall_post_scenario() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = parse_qs(request.content.decode("utf-8"))

        assert request.method == "POST"
        assert request.url.path == "/method/wall.post"
        assert payload["owner_id"] == ["-12345"]
        assert payload["message"] == ["Готовый материал для VK"]
        assert payload["access_token"] == ["vk-secret-token"]
        assert payload["v"] == ["5.199"]

        return httpx.Response(200, json={"response": {"post_id": 501}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        token_store = InMemoryPlatformTokenStore(PlatformTokenCipher(_encryption_key()))
        token_store.save_token(
            tenant_id="tenant-a",
            platform="vk",
            token="vk-secret-token",
        )
        bus = InMemoryEventBus()
        adapter = BasePlatformAdapter(
            platform="vk",
            publisher=VKWallPublisher(client=client),
            token_store=token_store,
            event_publisher=bus,
            sleeper=lambda delay: None,
        )

        receipt = await adapter.publish(
            PublicationRequest(
                tenant_id="tenant-a",
                platform="vk",
                publication_id="pub-vk-1",
                target_id="-12345",
                content="Готовый материал для VK",
                correlation_id="corr-vk-1",
            )
        )

    assert len(requests) == 1
    assert receipt.platform_ref_hash.startswith("sha256:")
    assert "vk-secret-token" not in bus.messages[-1].envelope.to_json()


def test_vk_adapter_maps_api_rate_limit_to_retryable_error() -> None:
    asyncio.run(_run_vk_rate_limit_error_scenario())


async def _run_vk_rate_limit_error_scenario() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Retry-After": "3"},
            json={
                "error": {
                    "error_code": 6,
                    "error_msg": "Too many requests per second",
                }
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        publisher = VKWallPublisher(client=client)

        with pytest.raises(PlatformPublicationError) as exc_info:
            await publisher.publish(
                PlatformPublishCommand(
                    tenant_id="tenant-a",
                    platform="vk",
                    publication_id="pub-vk-rate-limit",
                    target_id="-12345",
                    content="Материал",
                    correlation_id="corr-vk-rate-limit",
                    metadata={},
                    access_token=SecretStr("vk-secret-token"),
                    attempt=1,
                    requested_at=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
                )
            )

    assert exc_info.value.error_code == "rate_limited"
    assert exc_info.value.retryable is True
    assert exc_info.value.retry_after_seconds == 3.0
    assert "vk-secret-token" not in str(exc_info.value)
