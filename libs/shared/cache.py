from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Protocol, cast
from urllib.parse import urlparse

from libs.shared.tenant import TenantContext, require_tenant_context

REDIS_URL_ENV = "REDIS_URL"
REDIS_URL_SCHEMES = frozenset({"redis", "rediss"})
DEFAULT_CACHE_KEY_PREFIX = "nmc"

type JSONValue = (
    None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
)


class TenantCache(Protocol):
    async def get_json(
        self,
        namespace: str,
        key: str,
        *,
        context: TenantContext | None = None,
    ) -> JSONValue | None:
        """Read a tenant-scoped JSON cache value."""

    async def set_json(
        self,
        namespace: str,
        key: str,
        value: JSONValue,
        *,
        context: TenantContext | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        """Write a tenant-scoped JSON cache value."""

    async def delete(
        self,
        namespace: str,
        key: str,
        *,
        context: TenantContext | None = None,
    ) -> bool:
        """Delete one tenant-scoped cache key."""

    async def invalidate_namespace(
        self,
        namespace: str,
        *,
        context: TenantContext | None = None,
    ) -> int:
        """Delete all cache keys from a tenant-scoped namespace."""

    async def increment(
        self,
        namespace: str,
        key: str,
        *,
        context: TenantContext | None = None,
        amount: int = 1,
        ttl_seconds: int | None = None,
    ) -> int:
        """Increment a tenant-scoped counter."""

    async def acquire_lock(
        self,
        namespace: str,
        key: str,
        *,
        owner: str,
        context: TenantContext | None = None,
        ttl_seconds: int | None = None,
    ) -> bool:
        """Acquire a tenant-scoped lock if it is free."""

    async def release_lock(
        self,
        namespace: str,
        key: str,
        *,
        owner: str,
        context: TenantContext | None = None,
    ) -> bool:
        """Release a tenant-scoped lock owned by the provided owner."""


@dataclass(frozen=True, slots=True)
class CacheSettings:
    redis_url: str
    key_prefix: str = DEFAULT_CACHE_KEY_PREFIX
    default_ttl_seconds: int = 300
    lock_ttl_seconds: int = 30

    def __post_init__(self) -> None:
        if self.default_ttl_seconds <= 0:
            raise ValueError("default_ttl_seconds должен быть положительным")
        if self.lock_ttl_seconds <= 0:
            raise ValueError("lock_ttl_seconds должен быть положительным")

        object.__setattr__(self, "redis_url", validate_redis_url(self.redis_url))
        object.__setattr__(
            self,
            "key_prefix",
            _normalize_key_segment(self.key_prefix, "key_prefix"),
        )

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        env_var: str = REDIS_URL_ENV,
        key_prefix: str = DEFAULT_CACHE_KEY_PREFIX,
        default_ttl_seconds: int = 300,
        lock_ttl_seconds: int = 30,
    ) -> CacheSettings:
        return cls(
            redis_url=redis_url_from_env(environ, env_var=env_var),
            key_prefix=key_prefix,
            default_ttl_seconds=default_ttl_seconds,
            lock_ttl_seconds=lock_ttl_seconds,
        )


@dataclass(slots=True)
class _CacheEntry:
    value: JSONValue
    expires_at: float | None


@dataclass(slots=True)
class _LockEntry:
    owner: str
    expires_at: float


class InMemoryTenantCache:
    """Deterministic tenant cache for unit tests and local service wiring."""

    def __init__(
        self,
        *,
        key_prefix: str = DEFAULT_CACHE_KEY_PREFIX,
        default_ttl_seconds: int = 300,
        lock_ttl_seconds: int = 30,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if default_ttl_seconds <= 0:
            raise ValueError("default_ttl_seconds должен быть положительным")
        if lock_ttl_seconds <= 0:
            raise ValueError("lock_ttl_seconds должен быть положительным")

        self._key_prefix = _normalize_key_segment(key_prefix, "key_prefix")
        self._default_ttl_seconds = default_ttl_seconds
        self._lock_ttl_seconds = lock_ttl_seconds
        self._clock = clock or time.time
        self._entries: dict[str, _CacheEntry] = {}
        self._locks: dict[str, _LockEntry] = {}

    async def get_json(
        self,
        namespace: str,
        key: str,
        *,
        context: TenantContext | None = None,
    ) -> JSONValue | None:
        self._purge_expired()
        cache_key = build_tenant_cache_key(
            namespace,
            key,
            context=context,
            prefix=self._key_prefix,
        )
        entry = self._entries.get(cache_key)
        if entry is None:
            return None

        return _clone_json(entry.value)

    async def set_json(
        self,
        namespace: str,
        key: str,
        value: JSONValue,
        *,
        context: TenantContext | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        cache_key = build_tenant_cache_key(
            namespace,
            key,
            context=context,
            prefix=self._key_prefix,
        )
        self._entries[cache_key] = _CacheEntry(
            value=_clone_json(value),
            expires_at=self._expires_at(ttl_seconds),
        )

    async def delete(
        self,
        namespace: str,
        key: str,
        *,
        context: TenantContext | None = None,
    ) -> bool:
        cache_key = build_tenant_cache_key(
            namespace,
            key,
            context=context,
            prefix=self._key_prefix,
        )
        return self._entries.pop(cache_key, None) is not None

    async def invalidate_namespace(
        self,
        namespace: str,
        *,
        context: TenantContext | None = None,
    ) -> int:
        resolved_context = _resolve_context(context)
        normalized_namespace = _normalize_key_segment(namespace, "namespace")
        prefix = _tenant_cache_prefix(
            resolved_context,
            normalized_namespace,
            prefix=self._key_prefix,
        )
        keys_to_delete = [
            cache_key for cache_key in self._entries if cache_key.startswith(prefix)
        ]
        for cache_key in keys_to_delete:
            del self._entries[cache_key]

        return len(keys_to_delete)

    async def increment(
        self,
        namespace: str,
        key: str,
        *,
        context: TenantContext | None = None,
        amount: int = 1,
        ttl_seconds: int | None = None,
    ) -> int:
        cache_key = build_tenant_cache_key(
            namespace,
            key,
            context=context,
            prefix=self._key_prefix,
        )
        self._purge_expired()
        entry = self._entries.get(cache_key)
        current_value = 0
        if entry is not None:
            if not isinstance(entry.value, int) or isinstance(entry.value, bool):
                raise TypeError("cache counter должен хранить целое число")
            current_value = entry.value

        next_value = current_value + amount
        self._entries[cache_key] = _CacheEntry(
            value=next_value,
            expires_at=self._expires_at(ttl_seconds),
        )
        return next_value

    async def acquire_lock(
        self,
        namespace: str,
        key: str,
        *,
        owner: str,
        context: TenantContext | None = None,
        ttl_seconds: int | None = None,
    ) -> bool:
        lock_key = _build_tenant_lock_key(
            namespace,
            key,
            context=context,
            prefix=self._key_prefix,
        )
        self._purge_expired()
        if lock_key in self._locks:
            return False

        self._locks[lock_key] = _LockEntry(
            owner=_normalize_owner(owner),
            expires_at=self._clock()
            + self._positive_ttl(
                ttl_seconds,
                self._lock_ttl_seconds,
                "ttl_seconds",
            ),
        )
        return True

    async def release_lock(
        self,
        namespace: str,
        key: str,
        *,
        owner: str,
        context: TenantContext | None = None,
    ) -> bool:
        lock_key = _build_tenant_lock_key(
            namespace,
            key,
            context=context,
            prefix=self._key_prefix,
        )
        self._purge_expired()
        lock = self._locks.get(lock_key)
        if lock is None or lock.owner != _normalize_owner(owner):
            return False

        del self._locks[lock_key]
        return True

    def _expires_at(self, ttl_seconds: int | None) -> float:
        return self._clock() + self._positive_ttl(
            ttl_seconds,
            self._default_ttl_seconds,
            "ttl_seconds",
        )

    def _positive_ttl(
        self,
        ttl_seconds: int | None,
        default_ttl_seconds: int,
        label: str,
    ) -> int:
        ttl = default_ttl_seconds if ttl_seconds is None else ttl_seconds
        if ttl <= 0:
            raise ValueError(f"{label} должен быть положительным")

        return ttl

    def _purge_expired(self) -> None:
        now = self._clock()
        expired_keys = [
            cache_key
            for cache_key, entry in self._entries.items()
            if entry.expires_at is not None and entry.expires_at <= now
        ]
        for cache_key in expired_keys:
            del self._entries[cache_key]

        expired_locks = [
            lock_key for lock_key, lock in self._locks.items() if lock.expires_at <= now
        ]
        for lock_key in expired_locks:
            del self._locks[lock_key]


@dataclass(frozen=True, slots=True)
class RedisTenantCache:
    """Redis-backed implementation of the shared tenant cache contract."""

    client: Any
    settings: CacheSettings

    @classmethod
    def from_settings(cls, settings: CacheSettings) -> RedisTenantCache:
        redis_asyncio = cast(Any, import_module("redis.asyncio"))
        client = redis_asyncio.from_url(settings.redis_url, decode_responses=True)

        return cls(client=client, settings=settings)

    async def get_json(
        self,
        namespace: str,
        key: str,
        *,
        context: TenantContext | None = None,
    ) -> JSONValue | None:
        raw_value = await self.client.get(self._cache_key(namespace, key, context))
        if raw_value is None:
            return None

        return cast(JSONValue, json.loads(str(raw_value)))

    async def set_json(
        self,
        namespace: str,
        key: str,
        value: JSONValue,
        *,
        context: TenantContext | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        ttl = self.settings.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        if ttl <= 0:
            raise ValueError("ttl_seconds должен быть положительным")

        await self.client.set(
            self._cache_key(namespace, key, context),
            json.dumps(_clone_json(value), ensure_ascii=False, sort_keys=True),
            ex=ttl,
        )

    async def delete(
        self,
        namespace: str,
        key: str,
        *,
        context: TenantContext | None = None,
    ) -> bool:
        deleted = await self.client.delete(self._cache_key(namespace, key, context))
        return int(deleted) > 0

    async def invalidate_namespace(
        self,
        namespace: str,
        *,
        context: TenantContext | None = None,
    ) -> int:
        resolved_context = _resolve_context(context)
        normalized_namespace = _normalize_key_segment(namespace, "namespace")
        prefix = _tenant_cache_prefix(
            resolved_context,
            normalized_namespace,
            prefix=self.settings.key_prefix,
        )
        keys = [key async for key in self.client.scan_iter(match=f"{prefix}*")]
        if not keys:
            return 0

        deleted = await self.client.delete(*keys)
        return int(deleted)

    async def increment(
        self,
        namespace: str,
        key: str,
        *,
        context: TenantContext | None = None,
        amount: int = 1,
        ttl_seconds: int | None = None,
    ) -> int:
        cache_key = self._cache_key(namespace, key, context)
        next_value = await self.client.incrby(cache_key, amount)
        ttl = self.settings.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        if ttl <= 0:
            raise ValueError("ttl_seconds должен быть положительным")
        await self.client.expire(cache_key, ttl)

        return int(next_value)

    async def acquire_lock(
        self,
        namespace: str,
        key: str,
        *,
        owner: str,
        context: TenantContext | None = None,
        ttl_seconds: int | None = None,
    ) -> bool:
        ttl = self.settings.lock_ttl_seconds if ttl_seconds is None else ttl_seconds
        if ttl <= 0:
            raise ValueError("ttl_seconds должен быть положительным")

        result = await self.client.set(
            _build_tenant_lock_key(
                namespace,
                key,
                context=context,
                prefix=self.settings.key_prefix,
            ),
            _normalize_owner(owner),
            ex=ttl,
            nx=True,
        )
        return bool(result)

    async def release_lock(
        self,
        namespace: str,
        key: str,
        *,
        owner: str,
        context: TenantContext | None = None,
    ) -> bool:
        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] "
            "then return redis.call('del', KEYS[1]) else return 0 end"
        )
        deleted = await self.client.eval(
            script,
            1,
            _build_tenant_lock_key(
                namespace,
                key,
                context=context,
                prefix=self.settings.key_prefix,
            ),
            _normalize_owner(owner),
        )

        return int(deleted) == 1

    def _cache_key(
        self,
        namespace: str,
        key: str,
        context: TenantContext | None,
    ) -> str:
        return build_tenant_cache_key(
            namespace,
            key,
            context=context,
            prefix=self.settings.key_prefix,
        )


def redis_url_from_env(
    environ: Mapping[str, str] | None = None,
    *,
    env_var: str = REDIS_URL_ENV,
) -> str:
    source = os.environ if environ is None else environ
    redis_url = source.get(env_var)
    if redis_url is None or redis_url.strip() == "":
        raise ValueError(f"{env_var} должен быть задан")

    return validate_redis_url(redis_url)


def validate_redis_url(redis_url: str) -> str:
    normalized_url = redis_url.strip()
    if normalized_url == "":
        raise ValueError("REDIS_URL должен быть непустой строкой")

    parsed_url = urlparse(normalized_url)
    if parsed_url.scheme not in REDIS_URL_SCHEMES or parsed_url.netloc == "":
        raise ValueError("REDIS_URL должен использовать redis:// или rediss://")

    return normalized_url


def build_tenant_cache_key(
    namespace: str,
    key: str,
    *,
    context: TenantContext | None = None,
    prefix: str = DEFAULT_CACHE_KEY_PREFIX,
) -> str:
    resolved_context = _resolve_context(context)
    normalized_namespace = _normalize_key_segment(namespace, "namespace")
    normalized_key = _normalize_key_segment(key, "key")

    return (
        f"{_tenant_cache_prefix(resolved_context, normalized_namespace, prefix=prefix)}"
        f"{normalized_key}"
    )


def _tenant_cache_prefix(
    context: TenantContext,
    namespace: str,
    *,
    prefix: str,
) -> str:
    normalized_prefix = _normalize_key_segment(prefix, "prefix")
    normalized_tenant_id = _normalize_key_segment(context.tenant_id, "tenant_id")

    return f"{normalized_prefix}:tenant:{normalized_tenant_id}:{namespace}:"


def _build_tenant_lock_key(
    namespace: str,
    key: str,
    *,
    context: TenantContext | None = None,
    prefix: str,
) -> str:
    normalized_namespace = _normalize_key_segment(namespace, "namespace")
    return build_tenant_cache_key(
        f"locks.{normalized_namespace}",
        key,
        context=context,
        prefix=prefix,
    )


def _normalize_key_segment(value: str, label: str) -> str:
    normalized = value.strip()
    if normalized == "":
        raise ValueError(f"{label} должен быть непустой строкой")
    if ":" in normalized or any(character.isspace() for character in normalized):
        raise ValueError(f"{label} не должен содержать пробелы или ':'")

    return normalized


def _normalize_owner(owner: str) -> str:
    normalized = owner.strip()
    if normalized == "":
        raise ValueError("owner должен быть непустой строкой")

    return normalized


def _clone_json(value: JSONValue) -> JSONValue:
    return cast(JSONValue, json.loads(json.dumps(value, ensure_ascii=False)))


def _resolve_context(context: TenantContext | None) -> TenantContext:
    if context is not None:
        return context

    return require_tenant_context()
