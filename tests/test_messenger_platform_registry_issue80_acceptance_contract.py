from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime
from pathlib import Path

from messenger_adapter import (
    DEFAULT_PLATFORM_CATALOG_SIZE,
    BasePlatformAdapter,
    InMemoryPlatformPublisher,
    InMemoryPlatformTokenStore,
    PlatformStatus,
    PlatformTokenCipher,
    PublicationBatchRequest,
    UnifiedMessengerAdapter,
    build_default_platform_registry,
    default_platform_registry_entries,
)

ROOT = Path(__file__).resolve().parents[1]
TENANT_ID = "tenant-a"


def _encryption_key() -> str:
    return base64.b64encode(b"8" * 32).decode("ascii")


def test_issue_80_default_platform_catalog_has_102_prioritized_entries() -> None:
    entries = default_platform_registry_entries(tenant_id=TENANT_ID)

    assert len(entries) == DEFAULT_PLATFORM_CATALOG_SIZE == 102
    assert len({entry.platform for entry in entries}) == 102
    assert [entry.platform for entry in entries[:10]] == [
        "telegram",
        "vk",
        "dzen",
        "ok",
        "rutube",
        "vc",
        "pikabu",
        "habr",
        "tenchat",
        "livejournal",
    ]
    assert [entry.priority for entry in entries] == sorted(
        entry.priority for entry in entries
    )
    assert {entry.status for entry in entries} == {
        PlatformStatus.ACTIVE,
        PlatformStatus.PAUSED,
        PlatformStatus.DISABLED,
    }

    for entry in entries:
        assert entry.tenant_id == TENANT_ID
        assert entry.limits.max_text_length > 0
        assert entry.limits.max_media_items >= 0
        assert entry.parameters["display_name"]
        assert entry.parameters["category"]
        assert entry.parameters["default_target_id"] == f"nmc-{entry.platform}"
        assert entry.parameters["catalog_issue"] == "#80"


def test_issue_80_status_updates_change_available_routes() -> None:
    registry = build_default_platform_registry(tenant_id=TENANT_ID)
    initial_entries = registry.list_platforms(tenant_id=TENANT_ID)
    disabled_platform = initial_entries[0].platform
    promoted_platform = next(
        entry.platform
        for entry in initial_entries
        if entry.status == PlatformStatus.PAUSED
    )

    updated_at = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
    disabled_entry = registry.update_status(
        tenant_id=TENANT_ID,
        platform=disabled_platform,
        status=PlatformStatus.DISABLED,
        updated_at=updated_at,
    )
    promoted_entry = registry.update_status(
        tenant_id=TENANT_ID,
        platform=promoted_platform,
        status=PlatformStatus.ACTIVE,
        updated_at=updated_at,
    )

    active_platforms = [
        entry.platform
        for entry in registry.list_platforms(tenant_id=TENANT_ID)
        if entry.status == PlatformStatus.ACTIVE
    ]

    assert disabled_entry.status == PlatformStatus.DISABLED
    assert disabled_entry.updated_at == updated_at
    assert promoted_entry.status == PlatformStatus.ACTIVE
    assert promoted_entry.updated_at == updated_at
    assert disabled_platform not in active_platforms
    assert promoted_platform in active_platforms


def test_issue_80_catalog_routes_publication_by_priority_and_status() -> None:
    asyncio.run(_run_issue_80_catalog_routing_scenario())


async def _run_issue_80_catalog_routing_scenario() -> None:
    registry = build_default_platform_registry(tenant_id=TENANT_ID)
    blocked_platform = "dzen"
    promoted_platform = next(
        entry.platform
        for entry in registry.list_platforms(tenant_id=TENANT_ID)
        if entry.status == PlatformStatus.PAUSED
    )
    registry.update_status(
        tenant_id=TENANT_ID,
        platform=blocked_platform,
        status=PlatformStatus.DISABLED,
    )
    registry.update_status(
        tenant_id=TENANT_ID,
        platform=promoted_platform,
        status=PlatformStatus.ACTIVE,
    )

    token_store = InMemoryPlatformTokenStore(PlatformTokenCipher(_encryption_key()))
    publishers: dict[str, InMemoryPlatformPublisher] = {}
    adapters: dict[str, BasePlatformAdapter] = {}
    active_entries = [
        entry
        for entry in registry.list_platforms(tenant_id=TENANT_ID)
        if entry.status == PlatformStatus.ACTIVE
    ]

    for entry in active_entries:
        token_store.save_token(
            tenant_id=TENANT_ID,
            platform=entry.platform,
            token=f"{entry.platform}-token",
        )
        publisher = InMemoryPlatformPublisher(connector_name=f"{entry.platform}_mock")
        publishers[entry.platform] = publisher
        adapters[entry.platform] = BasePlatformAdapter(
            platform=entry.platform,
            publisher=publisher,
            token_store=token_store,
            platform_registry=registry,
            sleeper=lambda delay: None,
        )

    result = await UnifiedMessengerAdapter(
        platform_adapters=adapters,
        platform_registry=registry,
    ).publish(
        PublicationBatchRequest(
            tenant_id=TENANT_ID,
            publication_id="pub-issue-80",
            content="Готовый материал для каталога 102 площадок",
            correlation_id="corr-issue-80",
        )
    )

    expected_platforms = tuple(entry.platform for entry in active_entries)
    assert result.succeeded_platforms == expected_platforms
    assert result.failed == ()
    assert blocked_platform not in result.succeeded_platforms
    assert promoted_platform in result.succeeded_platforms

    for entry in active_entries:
        command = publishers[entry.platform].commands[0]
        assert command.target_id == f"nmc-{entry.platform}"
        assert command.content == "Готовый материал для каталога 102 площадок"


def test_issue_80_messenger_adapter_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/messenger-adapter.md").read_text(encoding="utf-8")
    readme = (ROOT / "services/messenger-adapter/README.md").read_text(encoding="utf-8")

    for marker in (
        "#80",
        "DEFAULT_PLATFORM_CATALOG_SIZE",
        "102 tenant-scoped записи",
        "build_default_platform_registry",
    ):
        assert marker in spec

    for marker in (
        "DEFAULT_PLATFORM_CATALOG_SIZE",
        "102 tenant-scoped записи",
        "build_default_platform_registry",
    ):
        assert marker in readme
