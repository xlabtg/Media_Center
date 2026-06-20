from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Protocol

from pydantic import Field, field_validator

from libs.shared.models import JSONValue, SharedBaseModel, TenantId
from messenger_adapter.content_transformer import (
    DEFAULT_PLATFORM_LIMITS,
    PlatformContentLimits,
)

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

    def update_status(
        self,
        *,
        tenant_id: str,
        platform: str,
        status: PlatformStatus | str,
        updated_at: datetime | None = None,
    ) -> PlatformRegistryEntry:
        """Update one tenant-owned platform availability status."""


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

    def update_status(
        self,
        *,
        tenant_id: str,
        platform: str,
        status: PlatformStatus | str,
        updated_at: datetime | None = None,
    ) -> PlatformRegistryEntry:
        entry = self.require_platform(tenant_id=tenant_id, platform=platform)
        updated_entry = entry.model_copy(
            update={
                "status": _normalize_status(status),
                "updated_at": _normalize_datetime(updated_at or datetime.now(UTC)),
            }
        )
        return self.upsert(updated_entry)


def _normalize_platform(platform: str) -> str:
    normalized = platform.strip().lower()
    if normalized == "":
        raise PlatformRegistryError("platform не может быть пустой")
    return normalized


DEFAULT_PLATFORM_CATALOG_SIZE = 102

_ACTIVE_CATALOG_ENTRY_COUNT = 82
_PAUSED_CATALOG_ENTRY_COUNT = 12

_DEFAULT_TARGET_PREFIX = "nmc"

_CATEGORY_LIMITS: dict[str, PlatformContentLimits] = {
    "audio": PlatformContentLimits(max_text_length=3_000, max_media_items=3),
    "blog": PlatformContentLimits(max_text_length=12_000, max_media_items=20),
    "books": PlatformContentLimits(max_text_length=12_000, max_media_items=5),
    "business": PlatformContentLimits(max_text_length=7_000, max_media_items=10),
    "classifieds": PlatformContentLimits(max_text_length=4_000, max_media_items=10),
    "community": PlatformContentLimits(max_text_length=6_000, max_media_items=10),
    "creator": PlatformContentLimits(max_text_length=5_000, max_media_items=10),
    "developer": PlatformContentLimits(max_text_length=8_000, max_media_items=5),
    "education": PlatformContentLimits(max_text_length=8_000, max_media_items=5),
    "forum": PlatformContentLimits(max_text_length=8_000, max_media_items=10),
    "jobs": PlatformContentLimits(max_text_length=4_000, max_media_items=5),
    "live": PlatformContentLimits(max_text_length=3_000, max_media_items=5),
    "maps": PlatformContentLimits(max_text_length=2_000, max_media_items=10),
    "marketing": PlatformContentLimits(max_text_length=7_000, max_media_items=10),
    "messenger": PlatformContentLimits(max_text_length=4_096, max_media_items=10),
    "microblog": PlatformContentLimits(max_text_length=2_000, max_media_items=4),
    "news": PlatformContentLimits(max_text_length=7_000, max_media_items=10),
    "podcast": PlatformContentLimits(max_text_length=3_000, max_media_items=3),
    "portfolio": PlatformContentLimits(max_text_length=5_000, max_media_items=20),
    "pr": PlatformContentLimits(max_text_length=6_000, max_media_items=5),
    "qna": PlatformContentLimits(max_text_length=6_000, max_media_items=5),
    "reviews": PlatformContentLimits(max_text_length=4_000, max_media_items=10),
    "social": PlatformContentLimits(max_text_length=5_000, max_media_items=10),
    "video": PlatformContentLimits(max_text_length=5_000, max_media_items=5),
}

_DEFAULT_PLATFORM_CATALOG: tuple[tuple[str, str, str], ...] = (
    ("telegram", "Telegram", "messenger"),
    ("vk", "VK", "social"),
    ("dzen", "Dzen", "blog"),
    ("ok", "Odnoklassniki", "social"),
    ("rutube", "RuTube", "video"),
    ("vc", "VC.ru", "blog"),
    ("pikabu", "Pikabu", "forum"),
    ("habr", "Habr", "developer"),
    ("tenchat", "TenChat", "business"),
    ("livejournal", "LiveJournal", "blog"),
    ("youtube", "YouTube", "video"),
    ("tiktok", "TikTok", "video"),
    ("instagram", "Instagram", "social"),
    ("facebook", "Facebook", "social"),
    ("twitter_x", "X / Twitter", "microblog"),
    ("threads", "Threads", "microblog"),
    ("linkedin", "LinkedIn", "business"),
    ("reddit", "Reddit", "forum"),
    ("pinterest", "Pinterest", "social"),
    ("twitch", "Twitch", "video"),
    ("discord", "Discord", "community"),
    ("whatsapp", "WhatsApp", "messenger"),
    ("viber", "Viber", "messenger"),
    ("line", "LINE", "messenger"),
    ("wechat", "WeChat", "messenger"),
    ("signal", "Signal", "messenger"),
    ("mastodon", "Mastodon", "microblog"),
    ("bluesky", "Bluesky", "microblog"),
    ("medium", "Medium", "blog"),
    ("substack", "Substack", "newsletter"),
    ("teletype", "Teletype", "blog"),
    ("yappy", "Yappy", "video"),
    ("tamtam", "TamTam", "messenger"),
    ("sferum", "Sferum", "messenger"),
    ("my_world", "My World", "social"),
    ("snapchat", "Snapchat", "social"),
    ("clubhouse", "Clubhouse", "audio"),
    ("quora", "Quora", "qna"),
    ("stackoverflow", "Stack Overflow", "developer"),
    ("github_discussions", "GitHub Discussions", "developer"),
    ("gitlab_forum", "GitLab Forum", "developer"),
    ("devto", "DEV Community", "developer"),
    ("hashnode", "Hashnode", "developer"),
    ("producthunt", "Product Hunt", "business"),
    ("behance", "Behance", "portfolio"),
    ("dribbble", "Dribbble", "portfolio"),
    ("vc_events", "VC.ru Events", "business"),
    ("spark", "Spark", "business"),
    ("cossa", "Cossa", "marketing"),
    ("sostav", "Sostav", "marketing"),
    ("pressfeed", "Pressfeed", "pr"),
    ("pulse", "Pulse", "news"),
    ("mailru_news", "Mail.ru News", "news"),
    ("rambler", "Rambler", "news"),
    ("rbc", "RBC", "news"),
    ("kommersant", "Kommersant", "news"),
    ("vedomosti", "Vedomosti", "news"),
    ("vc_news", "VC.ru News", "news"),
    ("sports_ru", "Sports.ru", "community"),
    ("championat", "Championat", "community"),
    ("drive2", "Drive2", "forum"),
    ("drom", "Drom", "forum"),
    ("forumhouse", "ForumHouse", "forum"),
    ("babyblog", "BabyBlog", "community"),
    ("otzovik", "Otzovik", "reviews"),
    ("irecommend", "IRecommend", "reviews"),
    ("zoon", "Zoon", "reviews"),
    ("yell", "Yell", "reviews"),
    ("flamp", "Flamp", "reviews"),
    ("yandex_maps", "Yandex Maps", "maps"),
    ("two_gis", "2GIS", "maps"),
    ("google_business", "Google Business Profile", "maps"),
    ("avito", "Avito", "classifieds"),
    ("youla", "Youla", "classifieds"),
    ("profi", "Profi", "classifieds"),
    ("hh_ru", "HH.ru", "jobs"),
    ("habr_career", "Habr Career", "jobs"),
    ("superjob", "SuperJob", "jobs"),
    ("geekjob", "GeekJob", "jobs"),
    ("finder_jobs", "Finder Jobs", "jobs"),
    ("boosty", "Boosty", "creator"),
    ("patreon", "Patreon", "creator"),
    ("donationalerts", "DonationAlerts", "creator"),
    ("vk_donut", "VK Donut", "creator"),
    ("podster", "Podster", "podcast"),
    ("soundcloud", "SoundCloud", "audio"),
    ("spotify_podcasts", "Spotify Podcasts", "podcast"),
    ("apple_podcasts", "Apple Podcasts", "podcast"),
    ("yandex_music", "Yandex Music", "audio"),
    ("promodj", "PromoDJ", "audio"),
    ("vimeo", "Vimeo", "video"),
    ("dailymotion", "Dailymotion", "video"),
    ("niconico", "Niconico", "video"),
    ("trovo", "Trovo", "live"),
    ("wasd", "WASD", "live"),
    ("goodgame", "GoodGame", "live"),
    ("peertube", "PeerTube", "video"),
    ("coub", "Coub", "video"),
    ("bookmate", "Bookmate", "books"),
    ("litnet", "Litnet", "books"),
    ("author_today", "Author.Today", "books"),
    ("stepik", "Stepik", "education"),
)


def default_platform_registry_entries(
    *,
    tenant_id: str,
    status_overrides: Mapping[str, PlatformStatus | str] | None = None,
    default_target_prefix: str = _DEFAULT_TARGET_PREFIX,
    updated_at: datetime | None = None,
) -> tuple[PlatformRegistryEntry, ...]:
    normalized_status_overrides = {
        _normalize_platform(platform): _normalize_status(status)
        for platform, status in (status_overrides or {}).items()
    }
    normalized_updated_at = _normalize_datetime(updated_at or datetime.now(UTC))
    target_prefix = default_target_prefix.strip()
    entries: list[PlatformRegistryEntry] = []

    for position, (platform, display_name, category) in enumerate(
        _DEFAULT_PLATFORM_CATALOG,
        start=1,
    ):
        normalized_platform = _normalize_platform(platform)
        status = normalized_status_overrides.get(
            normalized_platform,
            _catalog_status(position),
        )
        target_id = (
            f"{target_prefix}-{normalized_platform}"
            if target_prefix != ""
            else normalized_platform
        )
        parameters: dict[str, JSONValue] = {
            "display_name": display_name,
            "category": category,
            "routing_group": category,
            "default_target_id": target_id,
            "integration_profile": _integration_profile(normalized_platform),
            "status_source": "default_catalog_seed",
            "catalog_issue": "#80",
        }
        entries.append(
            PlatformRegistryEntry(
                tenant_id=tenant_id,
                platform=normalized_platform,
                status=status,
                priority=position * 10,
                limits=_limits_for_platform(
                    platform=normalized_platform,
                    category=category,
                ),
                parameters=parameters,
                updated_at=normalized_updated_at,
            )
        )

    return tuple(entries)


def build_default_platform_registry(
    *,
    tenant_id: str,
    status_overrides: Mapping[str, PlatformStatus | str] | None = None,
    default_target_prefix: str = _DEFAULT_TARGET_PREFIX,
    updated_at: datetime | None = None,
) -> InMemoryPlatformRegistry:
    return InMemoryPlatformRegistry(
        entries=default_platform_registry_entries(
            tenant_id=tenant_id,
            status_overrides=status_overrides,
            default_target_prefix=default_target_prefix,
            updated_at=updated_at,
        )
    )


def _catalog_status(position: int) -> PlatformStatus:
    if position <= _ACTIVE_CATALOG_ENTRY_COUNT:
        return PlatformStatus.ACTIVE
    if position <= _ACTIVE_CATALOG_ENTRY_COUNT + _PAUSED_CATALOG_ENTRY_COUNT:
        return PlatformStatus.PAUSED
    return PlatformStatus.DISABLED


def _integration_profile(platform: str) -> str:
    if platform in {"telegram", "vk", "dzen", "ok"}:
        return "native"
    if platform in {"rutube", "vc", "pikabu", "habr", "tenchat", "livejournal"}:
        return "registry_http"
    return "catalog_only"


def _limits_for_platform(*, platform: str, category: str) -> PlatformContentLimits:
    if platform in DEFAULT_PLATFORM_LIMITS:
        return DEFAULT_PLATFORM_LIMITS[platform]
    return _CATEGORY_LIMITS.get(
        category,
        PlatformContentLimits(max_text_length=5_000, max_media_items=10),
    )


def _normalize_status(status: PlatformStatus | str) -> PlatformStatus:
    if isinstance(status, PlatformStatus):
        return status
    try:
        return PlatformStatus(status.strip().lower())
    except ValueError as error:
        raise PlatformRegistryError("Неизвестный статус площадки") from error


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


if len(_DEFAULT_PLATFORM_CATALOG) != DEFAULT_PLATFORM_CATALOG_SIZE:
    raise RuntimeError("DEFAULT_PLATFORM_CATALOG должен содержать 102 площадки")


__all__ = [
    "DEFAULT_PLATFORM_CATALOG_SIZE",
    "InMemoryPlatformRegistry",
    "PlatformKey",
    "PlatformNotRegisteredError",
    "PlatformRegistry",
    "PlatformRegistryEntry",
    "PlatformRegistryError",
    "PlatformStatus",
    "build_default_platform_registry",
    "default_platform_registry_entries",
]
