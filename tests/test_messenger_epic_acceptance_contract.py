from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from messenger_adapter import (
    BasePlatformAdapter,
    InMemoryPlatformPublisher,
    InMemoryPlatformRegistry,
    InMemoryPlatformTokenStore,
    PlatformContentLimits,
    PlatformRegistryEntry,
    PlatformStatus,
    PlatformTokenCipher,
    PublicationBatchRequest,
    ReferralLinkInjector,
    UnifiedMessengerAdapter,
)

ROOT = Path(__file__).resolve().parents[1]


def _encryption_key() -> str:
    return base64.b64encode(b"4" * 32).decode("ascii")


def test_issue_48_unified_messenger_adapter_epic_acceptance_contract() -> None:
    asyncio.run(_run_issue_48_acceptance_scenario())


async def _run_issue_48_acceptance_scenario() -> None:
    registry = InMemoryPlatformRegistry(
        entries=[
            PlatformRegistryEntry(
                tenant_id="tenant-a",
                platform="telegram",
                status=PlatformStatus.ACTIVE,
                priority=10,
                limits=PlatformContentLimits(max_text_length=500, max_media_items=1),
                parameters={"default_target_id": "@nmc_channel"},
            ),
            PlatformRegistryEntry(
                tenant_id="tenant-a",
                platform="vk",
                status=PlatformStatus.ACTIVE,
                priority=20,
                limits=PlatformContentLimits(max_text_length=140, max_media_items=1),
                parameters={"default_target_id": "-12345"},
            ),
            PlatformRegistryEntry(
                tenant_id="tenant-a",
                platform="dzen",
                status=PlatformStatus.PAUSED,
                priority=30,
                limits=PlatformContentLimits(max_text_length=40, max_media_items=1),
                parameters={"default_target_id": "dzen-channel-1"},
            ),
        ]
    )
    token_store = InMemoryPlatformTokenStore(PlatformTokenCipher(_encryption_key()))
    token_store.save_token(
        tenant_id="tenant-a",
        platform="telegram",
        token="tg-secret-token",
    )
    token_store.save_token(tenant_id="tenant-a", platform="vk", token="vk-secret-token")

    telegram_publisher = InMemoryPlatformPublisher(connector_name="telegram_mock")
    vk_publisher = InMemoryPlatformPublisher(connector_name="vk_mock")
    telegram_adapter = BasePlatformAdapter(
        platform="telegram",
        publisher=telegram_publisher,
        token_store=token_store,
        platform_registry=registry,
        referral_link_injector=ReferralLinkInjector(),
        sleeper=lambda delay: None,
    )
    vk_adapter = BasePlatformAdapter(
        platform="vk",
        publisher=vk_publisher,
        token_store=token_store,
        platform_registry=registry,
        referral_link_injector=ReferralLinkInjector(),
        sleeper=lambda delay: None,
    )
    unified_adapter = UnifiedMessengerAdapter(
        platform_adapters={
            "telegram": telegram_adapter,
            "vk": vk_adapter,
        },
        platform_registry=registry,
    )

    result = await unified_adapter.publish(
        PublicationBatchRequest(
            tenant_id="tenant-a",
            publication_id="pub-issue-48",
            content=(
                "Готовый материал для единой публикации. "
                "VK должен получить обрезанную версию по лимиту реестра."
            ),
            correlation_id="corr-issue-48",
            metadata={
                "content_id": "content-issue-48",
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
                    "rotation_seed": "issue-48-acceptance",
                },
                "media": [
                    {"type": "image", "url": "https://example.test/1.jpg"},
                    {"type": "image", "url": "https://example.test/2.jpg"},
                ],
            },
        )
    )

    assert [receipt.platform for receipt in result.receipts] == ["telegram", "vk"]
    assert result.failed == ()
    assert result.publication_id == "pub-issue-48"

    telegram_command = telegram_publisher.commands[0]
    vk_command = vk_publisher.commands[0]
    assert telegram_command.target_id == "@nmc_channel"
    assert vk_command.target_id == "-12345"
    assert "Реферальные ссылки:" in telegram_command.content
    assert "Реферальные ссылки:" in vk_command.content
    assert len(vk_command.content) <= 140
    assert vk_command.metadata["media"] == [
        {"type": "image", "url": "https://example.test/1.jpg"}
    ]
    content_transform = vk_command.metadata["content_transform"]
    assert isinstance(content_transform, dict)
    assert content_transform["platform"] == "vk"
    assert "tg-secret-token" not in telegram_command.model_dump_json()
    assert "vk-secret-token" not in vk_command.model_dump_json()


def test_issue_48_unified_messenger_adapter_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/messenger-adapter.md").read_text(encoding="utf-8")
    readme = (ROOT / "services/messenger-adapter/README.md").read_text(encoding="utf-8")

    for marker in (
        "**Статус:** 🟢 реализовано",
        "UnifiedMessengerAdapter",
        "PublicationBatchRequest",
        "parameters.default_target_id",
        "#44",
        "#45",
        "#46",
        "#47",
        "#48",
        "Спецификация синхронизирована с реализацией Unified Messenger Adapter",
    ):
        assert marker in spec

    for marker in (
        "реализован минимальный контур Unified Messenger Adapter",
        "UnifiedMessengerAdapter",
        "PublicationBatchRequest",
    ):
        assert marker in readme
