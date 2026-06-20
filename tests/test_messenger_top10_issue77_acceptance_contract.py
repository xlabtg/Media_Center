from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
from messenger_adapter import (
    BasePlatformAdapter,
    DzenPostPublisher,
    InMemoryPlatformRegistry,
    InMemoryPlatformTokenStore,
    OKMediatopicPublisher,
    PlatformContentLimits,
    PlatformRegistryEntry,
    PlatformStatus,
    PlatformTokenCipher,
    PublicationBatchRequest,
    RegistryHTTPPublisher,
    RetryPolicy,
    UnifiedMessengerAdapter,
)
from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[1]
TENANT_ID = "tenant-a"


def _encryption_key() -> str:
    return base64.b64encode(b"7" * 32).decode("ascii")


def test_issue_77_top10_registry_publishes_to_dzen_ok_and_generic_platforms() -> None:
    asyncio.run(_run_issue_77_top10_publication_scenario())


async def _run_issue_77_top10_publication_scenario() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)

        if request.url.host == "dzen.example":
            payload = json.loads(request.content.decode("utf-8"))
            assert request.url.path == "/api/publications"
            assert request.headers["Authorization"] == "OAuth dzen-test-token"
            assert payload["channel_id"] == "dzen-channel-1"
            assert payload["text"].startswith("Готовый материал")
            assert payload["title"] == "Дайджест НМЦ"
            return httpx.Response(
                200,
                json={
                    "result": {
                        "publication_id": "dzen-77",
                        "published_at": "2026-06-20T12:00:00Z",
                    }
                },
            )

        if request.url.host == "ok.example":
            payload = parse_qs(request.content.decode("utf-8"))
            attachment = json.loads(payload["attachment"][0])
            assert request.url.path == "/fb.do"
            assert payload["method"] == ["mediatopic.post"]
            assert payload["access_token"] == ["ok-test-token"]
            assert payload["gid"] == ["ok-group-1"]
            assert attachment["media"][0]["text"].startswith("Готовый материал")
            return httpx.Response(200, json={"topic_id": "ok-77"})

        if request.url.host == "rutube.example":
            payload = json.loads(request.content.decode("utf-8"))
            assert request.url.path == "/api/posts"
            assert request.headers["Authorization"] == "Bearer rutube-test-token"
            assert request.headers["Idempotency-Key"] == "pub-issue-77"
            assert payload == {
                "channel_id": "rutube-channel-1",
                "text": "Готовый материал для расширенного набора площадок",
                "publication_id": "pub-issue-77",
                "correlation_id": "corr-issue-77",
                "title": "Видео-дайджест",
                "media": [{"type": "video", "url": "https://nmc.example/v/77"}],
            }
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "rutube-77",
                        "published_at": "2026-06-20T12:01:00Z",
                    }
                },
            )

        if request.url.host == "habr.example":
            payload = json.loads(request.content.decode("utf-8"))
            assert request.url.path == "/api/articles"
            assert payload["hub_id"] == "habr-hub-1"
            assert payload["text"].startswith("Готовый материал")
            return httpx.Response(
                429,
                headers={"Retry-After": "4"},
                json={"error_code": "rate_limited"},
            )

        return httpx.Response(404, json={"error": "unexpected host"})

    registry = InMemoryPlatformRegistry(
        entries=[
            PlatformRegistryEntry(
                tenant_id=TENANT_ID,
                platform="dzen",
                status=PlatformStatus.ACTIVE,
                priority=10,
                limits=PlatformContentLimits(max_text_length=1500, max_media_items=10),
                parameters={"default_target_id": "dzen-channel-1"},
            ),
            PlatformRegistryEntry(
                tenant_id=TENANT_ID,
                platform="ok",
                status=PlatformStatus.ACTIVE,
                priority=20,
                limits=PlatformContentLimits(max_text_length=4000, max_media_items=10),
                parameters={"default_target_id": "ok-group-1"},
            ),
            PlatformRegistryEntry(
                tenant_id=TENANT_ID,
                platform="rutube",
                status=PlatformStatus.ACTIVE,
                priority=30,
                limits=PlatformContentLimits(max_text_length=5000, max_media_items=5),
                parameters={
                    "default_target_id": "rutube-channel-1",
                    "http": {
                        "endpoint_url": "https://rutube.example/api/posts",
                        "auth_mode": "bearer",
                        "target_field": "channel_id",
                        "content_field": "text",
                        "media_field": "media",
                        "idempotency_header": "Idempotency-Key",
                        "publication_id_field": "publication_id",
                        "correlation_id_field": "correlation_id",
                        "response_ref_fields": ["id"],
                    },
                },
            ),
            PlatformRegistryEntry(
                tenant_id=TENANT_ID,
                platform="habr",
                status=PlatformStatus.ACTIVE,
                priority=40,
                limits=PlatformContentLimits(max_text_length=8000, max_media_items=3),
                parameters={
                    "default_target_id": "habr-hub-1",
                    "http": {
                        "endpoint_url": "https://habr.example/api/articles",
                        "target_field": "hub_id",
                        "content_field": "text",
                        "retryable_error_codes": ["rate_limited"],
                        "response_ref_fields": ["article_id", "id"],
                    },
                },
            ),
        ]
    )
    token_store = InMemoryPlatformTokenStore(PlatformTokenCipher(_encryption_key()))
    token_store.save_token(
        tenant_id=TENANT_ID,
        platform="dzen",
        token="dzen-test-token",
    )
    token_store.save_token(tenant_id=TENANT_ID, platform="ok", token="ok-test-token")
    token_store.save_token(
        tenant_id=TENANT_ID,
        platform="rutube",
        token="rutube-test-token",
    )
    token_store.save_token(
        tenant_id=TENANT_ID,
        platform="habr",
        token="habr-test-token",
    )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        generic_publisher = RegistryHTTPPublisher(
            platform_registry=registry,
            client=client,
        )
        unified_adapter = UnifiedMessengerAdapter(
            platform_adapters={
                "dzen": BasePlatformAdapter(
                    platform="dzen",
                    publisher=DzenPostPublisher(
                        client=client,
                        api_base_url="https://dzen.example/api",
                    ),
                    token_store=token_store,
                    platform_registry=registry,
                    sleeper=lambda delay: None,
                ),
                "ok": BasePlatformAdapter(
                    platform="ok",
                    publisher=OKMediatopicPublisher(
                        client=client,
                        api_base_url="https://ok.example/fb.do",
                        application_secret_key=SecretStr("ok-app-secret"),
                    ),
                    token_store=token_store,
                    platform_registry=registry,
                    sleeper=lambda delay: None,
                ),
                "rutube": BasePlatformAdapter(
                    platform="rutube",
                    publisher=generic_publisher,
                    token_store=token_store,
                    platform_registry=registry,
                    sleeper=lambda delay: None,
                ),
                "habr": BasePlatformAdapter(
                    platform="habr",
                    publisher=generic_publisher,
                    token_store=token_store,
                    retry_policy=RetryPolicy(max_attempts=1),
                    platform_registry=registry,
                    sleeper=lambda delay: None,
                ),
            },
            platform_registry=registry,
        )

        result = await unified_adapter.publish(
            PublicationBatchRequest(
                tenant_id=TENANT_ID,
                publication_id="pub-issue-77",
                content="Готовый материал для расширенного набора площадок",
                correlation_id="corr-issue-77",
                metadata={
                    "dzen": {"title": "Дайджест НМЦ"},
                    "rutube": {"title": "Видео-дайджест"},
                    "media": [
                        {
                            "type": "video",
                            "url": "https://nmc.example/v/77",
                        }
                    ],
                },
            )
        )

    assert [receipt.platform for receipt in result.receipts] == [
        "dzen",
        "ok",
        "rutube",
    ]
    assert result.failed_platforms == ("habr",)
    assert result.failed[0].error_code == "rate_limited"
    assert result.failed[0].retryable is True
    assert result.failed[0].attempt_count == 1
    assert [request.url.host for request in requests] == [
        "dzen.example",
        "ok.example",
        "rutube.example",
        "habr.example",
    ]
    result_json = result.model_dump_json()
    assert "dzen-test-token" not in result_json
    assert "ok-test-token" not in result_json
    assert "rutube-test-token" not in result_json
    assert "habr-test-token" not in result_json


def test_issue_77_messenger_adapter_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/messenger-adapter.md").read_text(encoding="utf-8")
    readme = (ROOT / "services/messenger-adapter/README.md").read_text(encoding="utf-8")
    compliance = (ROOT / "docs/COMPLIANCE.md").read_text(encoding="utf-8")

    for marker in (
        "#77",
        "RegistryHTTPPublisher",
        "parameters.http.endpoint_url",
        "Dzen/OK и дополнительные площадки top-10 РФ",
    ):
        assert marker in spec

    for marker in (
        "RegistryHTTPPublisher",
        "rutube",
        "habr",
        "top-10 РФ",
    ):
        assert marker in readme

    assert "Adapter policy: allowed/restricted/blocked" in compliance
