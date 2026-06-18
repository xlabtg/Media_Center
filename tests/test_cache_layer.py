from __future__ import annotations

import asyncio

import pytest

from libs.shared import (
    CacheSettings,
    InMemoryTenantCache,
    TenantContext,
    build_tenant_cache_key,
    redis_url_from_env,
)


def _context(tenant_id: str = "tenant-a") -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        subject="member-1",
        roles=("member_full",),
        correlation_id="corr-cache-1",
    )


def test_redis_url_from_env_requires_redis_scheme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(ValueError, match="REDIS_URL"):
        redis_url_from_env()

    monkeypatch.setenv("REDIS_URL", "postgresql://localhost/cache")
    with pytest.raises(ValueError, match="redis://"):
        redis_url_from_env()

    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    assert redis_url_from_env() == "redis://localhost:6379/0"


def test_cache_settings_normalize_safe_defaults() -> None:
    settings = CacheSettings(redis_url=" redis://localhost:6379/0 ")

    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.key_prefix == "nmc"
    assert settings.default_ttl_seconds == 300
    assert settings.lock_ttl_seconds == 30


def test_cache_keys_are_tenant_aware_and_reject_unsafe_segments() -> None:
    key = build_tenant_cache_key(
        "profiles",
        "member-1",
        context=_context("tenant-a"),
    )

    assert key == "nmc:tenant:tenant-a:profiles:member-1"

    with pytest.raises(ValueError, match="namespace"):
        build_tenant_cache_key("profiles:raw", "member-1", context=_context())


def test_in_memory_cache_round_trips_json_and_invalidates_namespace() -> None:
    asyncio.run(_run_cache_json_scenario())


async def _run_cache_json_scenario() -> None:
    context = _context()
    cache = InMemoryTenantCache()

    await cache.set_json(
        "profiles",
        "member-1",
        {"display_name": "Участник", "roles": ["member_full"]},
        context=context,
    )
    cached = await cache.get_json("profiles", "member-1", context=context)
    other_tenant_cached = await cache.get_json(
        "profiles",
        "member-1",
        context=_context("tenant-b"),
    )

    assert cached == {"display_name": "Участник", "roles": ["member_full"]}
    assert other_tenant_cached is None

    deleted_count = await cache.invalidate_namespace("profiles", context=context)

    assert deleted_count == 1
    assert await cache.get_json("profiles", "member-1", context=context) is None


def test_in_memory_cache_supports_counters_and_tenant_locks() -> None:
    asyncio.run(_run_cache_counter_lock_scenario())


async def _run_cache_counter_lock_scenario() -> None:
    context = _context()
    cache = InMemoryTenantCache()

    assert await cache.increment("rate-limit", "gateway", context=context) == 1
    assert (
        await cache.increment("rate-limit", "gateway", amount=4, context=context) == 5
    )
    assert (
        await cache.increment(
            "rate-limit",
            "gateway",
            context=_context("tenant-b"),
        )
        == 1
    )

    assert await cache.acquire_lock(
        "payouts",
        "distribution-1",
        owner="worker-1",
        context=context,
    )
    assert not await cache.acquire_lock(
        "payouts",
        "distribution-1",
        owner="worker-2",
        context=context,
    )
    assert not await cache.release_lock(
        "payouts",
        "distribution-1",
        owner="worker-2",
        context=context,
    )
    assert await cache.release_lock(
        "payouts",
        "distribution-1",
        owner="worker-1",
        context=context,
    )
