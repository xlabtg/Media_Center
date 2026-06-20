from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime
from pathlib import Path

from messenger_adapter import (
    BasePlatformAdapter,
    FallbackChannelPublisher,
    FallbackChannelRegistry,
    FallbackChannelRoute,
    FallbackChannelStatus,
    FallbackChannelType,
    InMemoryFallbackChannelRegistry,
    InMemoryPlatformTokenStore,
    InMemoryProxyLeaseProvider,
    PlatformPublicationError,
    PlatformPublishCommand,
    PlatformPublishResult,
    PlatformTokenCipher,
    PublicationBatchRequest,
    ResiliencePolicy,
    ResilientPlatformPublisher,
    RetryPolicy,
    UnifiedMessengerAdapter,
)

ROOT = Path(__file__).resolve().parents[1]
TENANT_ID = "tenant-a"


def _encryption_key() -> str:
    return base64.b64encode(b"9" * 32).decode("ascii")


def test_issue_81_proxy_rotation_and_fallback_channels_contract() -> None:
    asyncio.run(_run_issue_81_resilience_scenario())


async def _run_issue_81_resilience_scenario() -> None:
    proxy_provider = InMemoryProxyLeaseProvider()
    proxy_provider.register_pool(
        tenant_id=TENANT_ID,
        platform="telegram",
        proxies=(
            {
                "proxy_id": "http-primary",
                "protocol": "http",
                "url": "https://proxy-a.example:8443",
                "secret_ref": "vault://tenant-a/proxies/http-primary",
            },
            {
                "proxy_id": "socks-reserve",
                "protocol": "socks5",
                "url": "socks5://proxy-b.example:1080",
                "secret_ref": "vault://tenant-a/proxies/socks-reserve",
            },
        ),
    )
    fallback_registry: FallbackChannelRegistry = InMemoryFallbackChannelRegistry(
        routes=[
            FallbackChannelRoute(
                tenant_id=TENANT_ID,
                platform="telegram",
                channel_type=FallbackChannelType.IPFS,
                channel_id="ipfs-pinning",
                priority=10,
                endpoint="ipfs://tenant-a/media-center/outbox",
                secret_ref="vault://tenant-a/fallback/ipfs",
            ),
            FallbackChannelRoute(
                tenant_id=TENANT_ID,
                platform="telegram",
                channel_type=FallbackChannelType.TON,
                channel_id="ton-storage",
                priority=20,
                endpoint="ton://tenant-a/media-center/outbox",
                secret_ref="vault://tenant-a/fallback/ton",
            ),
            FallbackChannelRoute(
                tenant_id=TENANT_ID,
                platform="telegram",
                channel_type=FallbackChannelType.MATRIX,
                channel_id="matrix-room",
                priority=30,
                endpoint="matrix://room/nmc:fallback.example",
                secret_ref="vault://tenant-a/fallback/matrix",
            ),
        ]
    )
    fallback_publisher = ScriptedFallbackPublisher(failing_channels={"ipfs-pinning"})
    primary_publisher = ScriptedPublisher(
        outcomes=[
            PlatformPublicationError(
                "primary platform is unavailable",
                platform="telegram",
                error_code="platform_unavailable",
                retryable=True,
            )
        ]
    )
    resilient_publisher = ResilientPlatformPublisher(
        primary=primary_publisher,
        proxy_leases=proxy_provider,
        fallback_routes=fallback_registry,
        fallback_publisher=fallback_publisher,
        policy=ResiliencePolicy(
            proxy_pool_required=True,
            fallback_channel_types=(
                FallbackChannelType.IPFS,
                FallbackChannelType.TON,
                FallbackChannelType.MATRIX,
            ),
        ),
    )
    token_store = InMemoryPlatformTokenStore(PlatformTokenCipher(_encryption_key()))
    token_store.save_token(
        tenant_id=TENANT_ID,
        platform="telegram",
        token="tg-test-token",
    )
    adapter = BasePlatformAdapter(
        platform="telegram",
        publisher=resilient_publisher,
        token_store=token_store,
        retry_policy=RetryPolicy(max_attempts=1),
        sleeper=lambda delay: None,
    )
    result = await UnifiedMessengerAdapter(
        platform_adapters={"telegram": adapter}
    ).publish(
        PublicationBatchRequest(
            tenant_id=TENANT_ID,
            publication_id="pub-issue-81",
            platforms=("telegram",),
            target_ids={"telegram": "@nmc_channel"},
            content="Готовый материал для устойчивой доставки",
            correlation_id="corr-issue-81",
            metadata={"content_id": "content-81"},
        )
    )

    assert result.failed == ()
    assert result.succeeded_platforms == ("telegram",)
    assert result.receipts[0].attempt_count == 1
    assert proxy_provider.leased_proxy_ids == ("http-primary",)
    resilience_metadata = primary_publisher.commands[0].metadata["resilience"]
    assert isinstance(resilience_metadata, dict)
    proxy_metadata = resilience_metadata["proxy"]
    assert isinstance(proxy_metadata, dict)
    assert proxy_metadata == {
        "proxy_id": "http-primary",
        "protocol": "http",
        "redacted_url_hash": proxy_provider.leases[0].redacted_url_hash,
        "lease_id": proxy_provider.leases[0].lease_id,
    }
    assert tuple(fallback_publisher.attempted_channel_ids) == (
        "ipfs-pinning",
        "ton-storage",
    )
    assert fallback_registry.snapshot(
        tenant_id=TENANT_ID,
        platform="telegram",
    ).channel_statuses == {
        "ipfs-pinning": FallbackChannelStatus.UNHEALTHY,
        "ton-storage": FallbackChannelStatus.HEALTHY,
        "matrix-room": FallbackChannelStatus.HEALTHY,
    }
    fallback_result = resilient_publisher.fallback_results[-1]
    assert fallback_result.channel_type is FallbackChannelType.TON
    assert fallback_result.gateway_ref_hash.startswith("sha256:")
    assert fallback_result.content_hash.startswith("sha256:")

    result_json = result.model_dump_json()
    fallback_json = fallback_result.model_dump_json()
    assert "tg-test-token" not in result_json
    assert "proxy-a.example" not in result_json
    assert "vault://tenant-a/proxies/http-primary" not in result_json
    assert "ton://tenant-a/media-center/outbox" not in fallback_json
    assert "vault://tenant-a/fallback/ton" not in fallback_json


def test_issue_81_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/messenger-adapter.md").read_text(encoding="utf-8")
    readme = (ROOT / "services/messenger-adapter/README.md").read_text(encoding="utf-8")
    security = (ROOT / "docs/SECURITY.md").read_text(encoding="utf-8")
    compliance = (ROOT / "docs/COMPLIANCE.md").read_text(encoding="utf-8")

    for marker in (
        "#81",
        "ResilientPlatformPublisher",
        "FallbackChannelType.IPFS",
        "FallbackChannelType.TON",
        "FallbackChannelType.MATRIX",
    ):
        assert marker in spec

    for marker in (
        "ResilientPlatformPublisher",
        "IPFS/TON/Matrix",
        "proxy lease metadata",
    ):
        assert marker in readme

    for marker in (
        "Устойчивость интеграций",
        "legal fallback channels",
        "secret_ref_hash",
    ):
        assert marker in security

    assert "законная устойчивость каналов" in compliance


class ScriptedPublisher:
    def __init__(
        self,
        *,
        outcomes: list[PlatformPublishResult | PlatformPublicationError],
    ) -> None:
        self._outcomes = outcomes
        self.commands: list[PlatformPublishCommand] = []

    async def publish(
        self,
        command: PlatformPublishCommand,
    ) -> PlatformPublishResult:
        self.commands.append(command)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, PlatformPublicationError):
            raise outcome

        return outcome


class ScriptedFallbackPublisher(FallbackChannelPublisher):
    def __init__(self, *, failing_channels: set[str] | None = None) -> None:
        self._failing_channels = failing_channels or set()
        self.attempted_channel_ids: list[str] = []

    async def publish(
        self,
        command: PlatformPublishCommand,
        route: FallbackChannelRoute,
    ) -> PlatformPublishResult:
        self.attempted_channel_ids.append(route.channel_id)
        if route.channel_id in self._failing_channels:
            raise PlatformPublicationError(
                "fallback channel is unavailable",
                platform=command.platform,
                error_code="fallback_channel_unavailable",
                retryable=True,
            )

        result = PlatformPublishResult(
            platform=command.platform,
            platform_ref=f"{route.channel_type.value}:{route.channel_id}:pub-81",
            connector_name=f"{route.channel_type.value}_fallback",
            published_at=datetime(2026, 6, 20, 12, 0, tzinfo=UTC),
        )
        return result
