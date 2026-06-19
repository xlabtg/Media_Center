from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Protocol

from pydantic import Field, field_validator

from libs.shared.models import JSONValue, SharedBaseModel, TenantId
from messenger_adapter.content_transformer import PlatformContentLimits

_PLATFORM_PATTERN = r"^[a-z][a-z0-9_-]{1,63}$"

PlatformKey = Annotated[
    str,
    Field(
        min_length=2,
        max_length=64,
        pattern=_PLATFORM_PATTERN,
    ),
]


class PlatformStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"


class PlatformRegistryError(ValueError):
    """Base error for platform registry contract violations."""


class PlatformNotRegisteredError(LookupError):
    """Raised when a tenant has no requested platform registry entry."""


class PlatformRegistryEntry(SharedBaseModel):
    tenant_id: TenantId
    platform: PlatformKey
    limits: PlatformContentLimits
    priority: int = Field(default=100, ge=0, le=10_000)
    status: PlatformStatus = PlatformStatus.ACTIVE
    parameters: dict[str, JSONValue] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("platform", mode="before")
    @classmethod
    def _normalize_platform(cls, value: object) -> object:
        if isinstance(value, str):
            return _normalize_platform(value)
        return value

    @field_validator("updated_at")
    @classmethod
    def _normalize_updated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class PlatformRegistry(Protocol):
    def require_platform(
        self,
        *,
        tenant_id: str,
        platform: str,
    ) -> PlatformRegistryEntry:
        """Return a tenant-owned platform registry entry."""

    def list_platforms(self, *, tenant_id: str) -> tuple[PlatformRegistryEntry, ...]:
        """List tenant-owned platform entries sorted by priority."""


@dataclass(slots=True)
class InMemoryPlatformRegistry:
    entries: Iterable[PlatformRegistryEntry] = ()
    _entries: dict[tuple[str, str], PlatformRegistryEntry] = field(
        default_factory=dict,
        init=False,
    )

    def __post_init__(self) -> None:
        for entry in self.entries:
            self.upsert(entry)

    def upsert(self, entry: PlatformRegistryEntry) -> PlatformRegistryEntry:
        key = (entry.tenant_id, entry.platform)
        self._entries[key] = entry
        return entry

    def require_platform(
        self,
        *,
        tenant_id: str,
        platform: str,
    ) -> PlatformRegistryEntry:
        normalized_platform = _normalize_platform(platform)
        entry = self._entries.get((tenant_id, normalized_platform))
        if entry is None:
            raise PlatformNotRegisteredError("Площадка не зарегистрирована для tenant")
        return entry

    def list_platforms(self, *, tenant_id: str) -> tuple[PlatformRegistryEntry, ...]:
        entries = (entry for key, entry in self._entries.items() if key[0] == tenant_id)
        return tuple(
            sorted(entries, key=lambda entry: (entry.priority, entry.platform))
        )


def _normalize_platform(platform: str) -> str:
    normalized = platform.strip().lower()
    if normalized == "":
        raise PlatformRegistryError("platform не может быть пустой")
    return normalized


__all__ = [
    "InMemoryPlatformRegistry",
    "PlatformKey",
    "PlatformNotRegisteredError",
    "PlatformRegistry",
    "PlatformRegistryEntry",
    "PlatformRegistryError",
    "PlatformStatus",
]
