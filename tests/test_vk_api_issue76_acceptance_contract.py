from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import httpx
from messenger_adapter import (
    BasePlatformAdapter,
    InMemoryPlatformTokenStore,
    PlatformTokenCipher,
    PublicationRequest,
    VKAPIRateLimit,
    VKAPIRateLimiter,
    VKPostMetricsCollector,
    VKPostMetricsRequest,
    VKWallPublisher,
)

ROOT = Path(__file__).resolve().parents[1]
TENANT_ID = "tenant-a"


def _encryption_key() -> str:
    return base64.b64encode(b"6" * 32).decode("ascii")


def test_issue_76_vk_api_publishes_and_collects_post_metrics() -> None:
    asyncio.run(_run_issue_76_publish_and_metrics_scenario())


async def _run_issue_76_publish_and_metrics_scenario() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = parse_qs(request.content.decode("utf-8"))

        if request.url.path == "/method/wall.post":
            assert payload["owner_id"] == ["-12345"]
            assert payload["message"] == ["Готовый материал для VK"]
            assert payload["access_token"] == ["vk-test-token"]
            assert payload["attachments"] == [
                "photo-12345_1,https://nmc.example/material"
            ]
            assert payload["from_group"] == ["1"]
            assert payload["guid"] == ["pub-issue-76"]
            assert payload["v"] == ["5.199"]
            return httpx.Response(
                200,
                json={"response": {"post_id": 501, "date": 1781793600}},
            )

        if request.url.path == "/method/stats.getPostReach":
            assert payload["owner_id"] == ["-12345"]
            assert payload["post_ids"] == ["501"]
            assert payload["access_token"] == ["vk-test-token"]
            assert payload["v"] == ["5.199"]
            return httpx.Response(
                200,
                json={
                    "response": [
                        {
                            "post_id": 501,
                            "reach_total": 1200,
                            "reach_subscribers": 900,
                            "reach_viral": 250,
                            "reach_ads": 50,
                            "links": 31,
                            "to_group": 12,
                            "join_group": 5,
                            "hide": 2,
                            "report": 1,
                            "unsubscribe": 3,
                        }
                    ]
                },
            )

        if request.url.path == "/method/wall.getById":
            assert payload["posts"] == ["-12345_501"]
            assert payload["access_token"] == ["vk-test-token"]
            assert payload["v"] == ["5.199"]
            return httpx.Response(
                200,
                json={
                    "response": {
                        "items": [
                            {
                                "id": 501,
                                "owner_id": -12345,
                                "date": 1781793600,
                                "likes": {"count": 73},
                                "comments": {"count": 12},
                                "reposts": {"count": 8},
                                "views": {"count": 1450},
                            }
                        ]
                    }
                },
            )

        return httpx.Response(404, json={"error": "unexpected path"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        token_store = InMemoryPlatformTokenStore(PlatformTokenCipher(_encryption_key()))
        token_store.save_token(
            tenant_id=TENANT_ID,
            platform="vk",
            token="vk-test-token",
        )
        rate_limiter = VKAPIRateLimiter(
            limit=VKAPIRateLimit(
                max_requests_per_second=100,
                min_interval_seconds=0,
            ),
            sleeper=lambda delay: None,
        )
        adapter = BasePlatformAdapter(
            platform="vk",
            publisher=VKWallPublisher(client=client, rate_limiter=rate_limiter),
            token_store=token_store,
            sleeper=lambda delay: None,
        )

        receipt = await adapter.publish(
            PublicationRequest(
                tenant_id=TENANT_ID,
                platform="vk",
                publication_id="pub-issue-76",
                target_id="-12345",
                content="Готовый материал для VK",
                correlation_id="corr-vk-publish-76",
                metadata={
                    "vk": {
                        "attachments": [
                            "photo-12345_1",
                            "https://nmc.example/material",
                        ],
                        "from_group": True,
                        "guid": "pub-issue-76",
                    }
                },
            ),
            now=datetime(2026, 6, 20, 12, 0, tzinfo=UTC),
        )

        collector = VKPostMetricsCollector(
            client=client,
            token_store=token_store,
            rate_limiter=rate_limiter,
        )
        batch = await collector.collect(
            VKPostMetricsRequest(
                tenant_id=TENANT_ID,
                target_id="-12345",
                post_ids=(501,),
                correlation_id="corr-vk-metrics-76",
            ),
            now=datetime(2026, 6, 20, 12, 10, tzinfo=UTC),
        )

    assert [request.url.path for request in requests] == [
        "/method/wall.post",
        "/method/stats.getPostReach",
        "/method/wall.getById",
    ]
    assert receipt.platform_ref_hash.startswith("sha256:")
    assert receipt.published_at == datetime.fromtimestamp(1781793600, tz=UTC)
    assert batch.failed == ()
    assert len(batch.metrics) == 1
    metric = batch.metrics[0]
    assert metric.post_id == 501
    assert metric.reach_total == 1200
    assert metric.reach_subscribers == 900
    assert metric.views == 1450
    assert metric.likes == 73
    assert metric.comments == 12
    assert metric.reposts == 8
    assert metric.links == 31
    assert metric.engagement_count == 124
    assert metric.platform_ref_hash.startswith("sha256:")
    result_json = batch.model_dump_json()
    assert "vk-test-token" not in result_json
    assert "Готовый материал" not in result_json


def test_issue_76_vk_api_rate_limiter_paces_per_target_and_action() -> None:
    asyncio.run(_run_issue_76_rate_limit_scenario())


async def _run_issue_76_rate_limit_scenario() -> None:
    recorded_delays: list[float] = []
    limiter = VKAPIRateLimiter(
        limit=VKAPIRateLimit(
            max_requests_per_second=60,
            min_interval_seconds=5,
        ),
        sleeper=recorded_delays.append,
    )
    first_at = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)

    first_delay = await limiter.acquire(
        tenant_id=TENANT_ID,
        target_ref="-12345",
        action="wall.post",
        now=first_at,
    )
    second_delay = await limiter.acquire(
        tenant_id=TENANT_ID,
        target_ref="-12345",
        action="wall.post",
        now=first_at + timedelta(seconds=1),
    )
    another_action_delay = await limiter.acquire(
        tenant_id=TENANT_ID,
        target_ref="-12345",
        action="stats.getPostReach",
        now=first_at + timedelta(seconds=1),
    )
    another_target_delay = await limiter.acquire(
        tenant_id=TENANT_ID,
        target_ref="-67890",
        action="wall.post",
        now=first_at + timedelta(seconds=1),
    )

    assert first_delay == 0
    assert second_delay == 4
    assert another_action_delay == 0
    assert another_target_delay == 0
    assert recorded_delays == [4]


def test_issue_76_vk_api_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/messenger-adapter.md").read_text(encoding="utf-8")
    readme = (ROOT / "services/messenger-adapter/README.md").read_text(encoding="utf-8")
    security = (ROOT / "docs/SECURITY.md").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    for marker in (
        "#76",
        "VKPostMetricsCollector",
        "VKAPIRateLimiter",
        "stats.getPostReach",
        "wall.getById",
    ):
        assert marker in spec

    for marker in (
        "VK API",
        "VKPostMetricsCollector",
        "VKAPIRateLimiter",
        "reach_total",
    ):
        assert marker in readme

    assert "VK API" in security
    assert "VK_RATE_LIMIT_REQUESTS_PER_SECOND" in env_example
