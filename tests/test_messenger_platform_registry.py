from __future__ import annotations

import asyncio
import base64

import pytest
from messenger_adapter import (
    BasePlatformAdapter,
    InMemoryPlatformPublisher,
    InMemoryPlatformRegistry,
    InMemoryPlatformTokenStore,
    PlatformContentLimits,
    PlatformPublicationError,
    PlatformRegistryEntry,
    PlatformStatus,
    PlatformTokenCipher,
    PublicationRequest,
    ReferralLinkInjector,
)


def _encryption_key() -> str:
    return base64.b64encode(b"3" * 32).decode("ascii")


def test_platform_registry_tracks_limits_status_and_tenant_scope() -> None:
    registry = InMemoryPlatformRegistry()

    registry.upsert(
        PlatformRegistryEntry(
            tenant_id="tenant-a",
            platform="telegram",
            status=PlatformStatus.ACTIVE,
            priority=10,
            limits=PlatformContentLimits(
                max_text_length=128,
                max_media_items=3,
                max_link_items=1,
            ),
            parameters={"post_mode": "text"},
        )
    )
    registry.upsert(
        PlatformRegistryEntry(
            tenant_id="tenant-a",
            platform="vk",
            status=PlatformStatus.DISABLED,
            priority=20,
            limits=PlatformContentLimits(max_text_length=256),
        )
    )
    registry.upsert(
        PlatformRegistryEntry(
            tenant_id="tenant-b",
            platform="telegram",
            status=PlatformStatus.ACTIVE,
            priority=1,
            limits=PlatformContentLimits(max_text_length=4096),
        )
    )

    tenant_a_platforms = registry.list_platforms(tenant_id="tenant-a")

    assert [entry.platform for entry in tenant_a_platforms] == ["telegram", "vk"]
    assert (
        registry.require_platform(
            tenant_id="tenant-a",
            platform="TELEGRAM",
        ).limits.max_text_length
        == 128
    )
    assert (
        registry.require_platform(
            tenant_id="tenant-b",
            platform="telegram",
        ).limits.max_text_length
        == 4096
    )


def test_registry_status_blocks_publication_before_token_lookup() -> None:
    asyncio.run(_run_registry_status_scenario())


async def _run_registry_status_scenario() -> None:
    registry = InMemoryPlatformRegistry()
    registry.upsert(
        PlatformRegistryEntry(
            tenant_id="tenant-a",
            platform="vk",
            status=PlatformStatus.DISABLED,
            limits=PlatformContentLimits(max_text_length=100),
        )
    )
    publisher = InMemoryPlatformPublisher()
    token_store = InMemoryPlatformTokenStore(PlatformTokenCipher(_encryption_key()))
    adapter = BasePlatformAdapter(
        platform="vk",
        publisher=publisher,
        token_store=token_store,
        platform_registry=registry,
        sleeper=lambda delay: None,
    )

    with pytest.raises(PlatformPublicationError) as exc_info:
        await adapter.publish(
            PublicationRequest(
                tenant_id="tenant-a",
                platform="vk",
                publication_id="pub-disabled",
                target_id="group-1",
                content="Материал",
                correlation_id="corr-disabled",
            )
        )

    assert exc_info.value.error_code == "platform_disabled"
    assert exc_info.value.retryable is False
    assert publisher.commands == ()


def test_adapter_injects_cglr_referral_links_and_registry_limits() -> None:
    asyncio.run(_run_referral_injection_scenario())


async def _run_referral_injection_scenario() -> None:
    registry = InMemoryPlatformRegistry()
    registry.upsert(
        PlatformRegistryEntry(
            tenant_id="tenant-a",
            platform="telegram",
            status=PlatformStatus.ACTIVE,
            priority=10,
            limits=PlatformContentLimits(max_text_length=900, max_media_items=2),
        )
    )
    publisher = InMemoryPlatformPublisher()
    token_store = InMemoryPlatformTokenStore(PlatformTokenCipher(_encryption_key()))
    token_store.save_token(
        tenant_id="tenant-a",
        platform="telegram",
        token="tg-secret-token",
    )
    adapter = BasePlatformAdapter(
        platform="telegram",
        publisher=publisher,
        token_store=token_store,
        platform_registry=registry,
        referral_link_injector=ReferralLinkInjector(),
        sleeper=lambda delay: None,
    )

    await adapter.publish(
        PublicationRequest(
            tenant_id="tenant-a",
            platform="telegram",
            publication_id="pub-ref-links",
            target_id="@nmc_channel",
            content="Готовый материал",
            correlation_id="corr-ref-links",
            metadata={
                "content_id": "content-ref-links",
                "referral_route": {
                    "admin_link": {
                        "owner_id": "admin-main",
                        "url": "https://nmc.example/join",
                    },
                    "author_link": {
                        "owner_id": "author-7",
                        "url": "https://authors.example/author-7",
                    },
                    "l3_candidates": [
                        {
                            "owner_id": "partner-b",
                            "url": "https://partners.example/b",
                            "contribution_weight": 30,
                        }
                    ],
                },
                "media": [
                    {"type": "image", "url": "https://example.test/1.jpg"},
                    {"type": "image", "url": "https://example.test/2.jpg"},
                    {"type": "image", "url": "https://example.test/3.jpg"},
                ],
            },
        )
    )

    command = publisher.commands[0]
    assert command.content.startswith("Готовый материал")
    assert "Реферальные ссылки:" in command.content
    assert "L1: https://nmc.example/join?" in command.content
    assert "L2: https://authors.example/author-7?" in command.content
    assert "L3: https://partners.example/b?" in command.content
    assert "nmc_content_id=content-ref-links" in command.content
    assert "referral_route" not in command.metadata
    assert command.metadata["media"] == [
        {"type": "image", "url": "https://example.test/1.jpg"},
        {"type": "image", "url": "https://example.test/2.jpg"},
    ]
    assert command.metadata["referral_links"] == [
        {
            "level": "L1",
            "owner_id": "admin-main",
            "reward_share": 0.2,
        },
        {
            "level": "L2",
            "owner_id": "author-7",
            "reward_share": 0.1,
        },
        {
            "level": "L3",
            "owner_id": "partner-b",
            "reward_share": 0.05,
        },
    ]
    content_transform = command.metadata["content_transform"]
    assert isinstance(content_transform, dict)
    assert content_transform["platform"] == "telegram"
