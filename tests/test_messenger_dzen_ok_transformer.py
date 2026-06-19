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
    DzenPostPublisher,
    InMemoryPlatformPublisher,
    InMemoryPlatformTokenStore,
    OKMediatopicPublisher,
    PlatformContentLimits,
    PlatformContentTransformer,
    PlatformPublicationError,
    PlatformPublishCommand,
    PlatformTokenCipher,
    PublicationRequest,
)
from pydantic import SecretStr


def _encryption_key() -> str:
    return base64.b64encode(b"2" * 32).decode("ascii")


def test_content_transformer_truncates_text_and_media_for_dzen() -> None:
    transformer = PlatformContentTransformer(
        limits_by_platform={
            "dzen": PlatformContentLimits(
                max_text_length=40,
                max_media_items=2,
                max_link_items=1,
            )
        }
    )

    result = transformer.transform(
        platform="dzen",
        content="Первый абзац материала, который должен быть аккуратно обрезан",
        metadata={
            "media": [
                {"type": "link", "url": "https://example.test/one"},
                {"type": "link", "url": "https://example.test/two"},
                {"type": "image", "url": "https://example.test/image.jpg"},
            ],
        },
    )

    assert len(result.content) <= 40
    assert result.content.endswith("...")
    assert result.metadata["media"] == [
        {"type": "link", "url": "https://example.test/one"},
        {"type": "image", "url": "https://example.test/image.jpg"},
    ]
    assert result.metadata["content_transform"] == {
        "platform": "dzen",
        "text_truncated": True,
        "media_truncated": True,
        "original_text_length": 61,
        "transformed_text_length": len(result.content),
        "original_media_count": 3,
        "transformed_media_count": 2,
    }


def test_base_adapter_applies_transformer_before_publish() -> None:
    asyncio.run(_run_base_adapter_transformer_scenario())


async def _run_base_adapter_transformer_scenario() -> None:
    publisher = InMemoryPlatformPublisher()
    token_store = InMemoryPlatformTokenStore(PlatformTokenCipher(_encryption_key()))
    token_store.save_token(
        tenant_id="tenant-a",
        platform="dzen",
        token="dzen-secret-token",
    )
    adapter = BasePlatformAdapter(
        platform="dzen",
        publisher=publisher,
        token_store=token_store,
        content_transformer=PlatformContentTransformer(
            limits_by_platform={
                "dzen": PlatformContentLimits(max_text_length=20, max_media_items=1)
            }
        ),
        sleeper=lambda delay: None,
    )

    await adapter.publish(
        PublicationRequest(
            tenant_id="tenant-a",
            platform="dzen",
            publication_id="pub-dzen-transform",
            target_id="dzen-channel-1",
            content="Материал длиннее лимита площадки",
            correlation_id="corr-dzen-transform",
            metadata={
                "media": [
                    {"type": "image", "url": "https://example.test/1.jpg"},
                    {"type": "image", "url": "https://example.test/2.jpg"},
                ],
            },
        )
    )

    command = publisher.commands[0]
    assert len(command.content) <= 20
    assert command.content.endswith("...")
    assert command.metadata["media"] == [
        {"type": "image", "url": "https://example.test/1.jpg"}
    ]
    content_transform = command.metadata["content_transform"]
    assert isinstance(content_transform, dict)
    assert content_transform["platform"] == "dzen"
    assert "dzen-secret-token" not in json.dumps(command.metadata, ensure_ascii=False)


def test_dzen_adapter_posts_transformed_payload() -> None:
    asyncio.run(_run_dzen_publish_scenario())


async def _run_dzen_publish_scenario() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content.decode("utf-8"))

        assert request.method == "POST"
        assert request.url.path == "/api/publications"
        assert request.headers["Authorization"] == "OAuth dzen-secret-token"
        assert request.headers["Idempotency-Key"] == "pub-dzen-1"
        assert payload == {
            "channel_id": "dzen-channel-1",
            "text": "Готовый материал для Dzen",
            "title": "Новый дайджест",
            "media": [{"type": "image", "url": "https://example.test/dzen.jpg"}],
        }

        return httpx.Response(
            200,
            json={
                "publication_id": "dzen-post-42",
                "published_at": "2026-06-18T12:00:00Z",
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        publisher = DzenPostPublisher(
            client=client,
            api_base_url="https://dzen.example/api",
        )
        result = await publisher.publish(
            PlatformPublishCommand(
                tenant_id="tenant-a",
                platform="dzen",
                publication_id="pub-dzen-1",
                target_id="dzen-channel-1",
                content="Готовый материал для Dzen",
                correlation_id="corr-dzen-1",
                metadata={
                    "dzen": {"title": "Новый дайджест"},
                    "media": [
                        {
                            "type": "image",
                            "url": "https://example.test/dzen.jpg",
                        }
                    ],
                },
                access_token=SecretStr("dzen-secret-token"),
                attempt=1,
                requested_at=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
            )
        )

    assert len(requests) == 1
    assert result.platform_ref == "dzen-channel-1:dzen-post-42"
    assert result.published_at == datetime(2026, 6, 18, 12, 0, tzinfo=UTC)


def test_ok_adapter_posts_signed_mediatopic_attachment() -> None:
    asyncio.run(_run_ok_publish_scenario())


async def _run_ok_publish_scenario() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = parse_qs(request.content.decode("utf-8"))
        attachment = json.loads(payload["attachment"][0])

        assert request.method == "POST"
        assert request.url.path == "/fb.do"
        assert payload["method"] == ["mediatopic.post"]
        assert payload["format"] == ["json"]
        assert payload["access_token"] == ["ok-secret-token"]
        assert payload["application_key"] == ["ok-app-key"]
        assert payload["type"] == ["GROUP_THEME"]
        assert payload["gid"] == ["group-1"]
        assert "sig" in payload
        assert attachment == {
            "media": [
                {"type": "text", "text": "Готовый материал для OK"},
                {"type": "link", "url": "https://example.test/ok"},
            ]
        }

        return httpx.Response(200, json="topic-77")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        publisher = OKMediatopicPublisher(
            client=client,
            application_key="ok-app-key",
            application_secret_key=SecretStr("ok-app-secret"),
        )
        result = await publisher.publish(
            PlatformPublishCommand(
                tenant_id="tenant-a",
                platform="ok",
                publication_id="pub-ok-1",
                target_id="group-1",
                content="Готовый материал для OK",
                correlation_id="corr-ok-1",
                metadata={
                    "media": [{"type": "link", "url": "https://example.test/ok"}]
                },
                access_token=SecretStr("ok-secret-token"),
                attempt=1,
                requested_at=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
            )
        )

    assert len(requests) == 1
    assert result.platform_ref == "group-1:topic-77"


def test_ok_adapter_maps_http_rate_limit_to_retryable_error() -> None:
    asyncio.run(_run_ok_rate_limit_scenario())


async def _run_ok_rate_limit_scenario() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"Retry-After": "5"},
            json={"error_code": 429, "error_msg": "Too many requests"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        publisher = OKMediatopicPublisher(client=client)

        with pytest.raises(PlatformPublicationError) as exc_info:
            await publisher.publish(
                PlatformPublishCommand(
                    tenant_id="tenant-a",
                    platform="ok",
                    publication_id="pub-ok-rate-limit",
                    target_id="group-1",
                    content="Материал",
                    correlation_id="corr-ok-rate-limit",
                    metadata={},
                    access_token=SecretStr("ok-secret-token"),
                    attempt=1,
                    requested_at=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
                )
            )

    assert exc_info.value.error_code == "rate_limited"
    assert exc_info.value.retryable is True
    assert exc_info.value.retry_after_seconds == 5.0
    assert "ok-secret-token" not in str(exc_info.value)
