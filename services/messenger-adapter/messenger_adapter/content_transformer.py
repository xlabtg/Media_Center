from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

from pydantic import Field

from libs.shared.models import JSONValue, SharedBaseModel


class PlatformContentLimits(SharedBaseModel):
    max_text_length: int = Field(ge=1, le=100_000)
    max_media_items: int = Field(default=10, ge=0, le=100)
    max_link_items: int = Field(default=1, ge=0, le=20)


class TransformedContent(SharedBaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    metadata: dict[str, JSONValue] = Field(default_factory=dict)


DEFAULT_PLATFORM_LIMITS: dict[str, PlatformContentLimits] = {
    "telegram": PlatformContentLimits(max_text_length=4096, max_media_items=10),
    "vk": PlatformContentLimits(max_text_length=16_384, max_media_items=10),
    "dzen": PlatformContentLimits(max_text_length=1500, max_media_items=10),
    "ok": PlatformContentLimits(max_text_length=4000, max_media_items=10),
    "rutube": PlatformContentLimits(max_text_length=5000, max_media_items=5),
    "vc": PlatformContentLimits(max_text_length=8000, max_media_items=8),
    "pikabu": PlatformContentLimits(max_text_length=8000, max_media_items=10),
    "habr": PlatformContentLimits(max_text_length=8000, max_media_items=3),
    "tenchat": PlatformContentLimits(max_text_length=7000, max_media_items=10),
    "livejournal": PlatformContentLimits(max_text_length=12_000, max_media_items=20),
}


@dataclass(frozen=True, slots=True)
class PlatformContentTransformer:
    limits_by_platform: Mapping[str, PlatformContentLimits] = field(
        default_factory=lambda: DEFAULT_PLATFORM_LIMITS
    )
    default_limits: PlatformContentLimits = field(
        default_factory=lambda: PlatformContentLimits(
            max_text_length=100_000,
            max_media_items=100,
            max_link_items=20,
        )
    )

    def transform(
        self,
        *,
        platform: str,
        content: str,
        metadata: Mapping[str, JSONValue],
    ) -> TransformedContent:
        normalized_platform = _normalize_platform(platform)
        limits = self._limits_for(normalized_platform)
        transformed_content = smart_truncate(content, limits.max_text_length)

        transformed_metadata = clone_json_object(metadata)
        media_items = media_items_from_metadata(
            transformed_metadata,
            platform=normalized_platform,
        )
        transformed_media = limit_media_items(media_items, limits=limits)
        if media_items or "media" in transformed_metadata:
            transformed_metadata["media"] = [dict(item) for item in transformed_media]

        transformed_metadata["content_transform"] = {
            "platform": normalized_platform,
            "text_truncated": transformed_content != content,
            "media_truncated": len(transformed_media) != len(media_items),
            "original_text_length": len(content),
            "transformed_text_length": len(transformed_content),
            "original_media_count": len(media_items),
            "transformed_media_count": len(transformed_media),
        }
        return TransformedContent(
            content=transformed_content,
            metadata=transformed_metadata,
        )

    def _limits_for(self, platform: str) -> PlatformContentLimits:
        normalized_limits = {
            _normalize_platform(name): limits
            for name, limits in self.limits_by_platform.items()
        }
        return normalized_limits.get(platform, self.default_limits)


def smart_truncate(content: str, max_length: int) -> str:
    if max_length < 1:
        raise ValueError("max_length должен быть больше 0")
    if len(content) <= max_length:
        return content
    if max_length <= 3:
        return content[:max_length]

    available_length = max_length - 3
    candidate = content[:available_length].rstrip()
    boundary = max(
        candidate.rfind(" "),
        candidate.rfind("\n"),
        candidate.rfind("\t"),
    )
    if boundary >= max(1, int(available_length * 0.6)):
        candidate = candidate[:boundary].rstrip()
    if candidate == "":
        candidate = content[:available_length]

    return f"{candidate}..."


def media_items_from_metadata(
    metadata: Mapping[str, JSONValue],
    *,
    platform: str | None = None,
) -> tuple[dict[str, JSONValue], ...]:
    media_value = metadata.get("media")
    if media_value is None and platform is not None:
        platform_metadata = metadata.get(_normalize_platform(platform))
        if isinstance(platform_metadata, dict):
            media_value = platform_metadata.get("media")

    if not isinstance(media_value, list):
        return ()

    items: list[dict[str, JSONValue]] = []
    for item in media_value:
        normalized_item = _normalize_media_item(item)
        if normalized_item is not None:
            items.append(normalized_item)

    return tuple(items)


def limit_media_items(
    media_items: tuple[dict[str, JSONValue], ...],
    *,
    limits: PlatformContentLimits,
) -> tuple[dict[str, JSONValue], ...]:
    if limits.max_media_items == 0:
        return ()

    selected: list[dict[str, JSONValue]] = []
    link_count = 0
    for item in media_items:
        if _is_link_media(item):
            if link_count >= limits.max_link_items:
                continue
            link_count += 1

        selected.append(item)
        if len(selected) >= limits.max_media_items:
            break

    return tuple(selected)


def clone_json_object(value: Mapping[str, JSONValue]) -> dict[str, JSONValue]:
    return cast(
        dict[str, JSONValue],
        json.loads(json.dumps(value, ensure_ascii=False)),
    )


def _normalize_media_item(item: JSONValue) -> dict[str, JSONValue] | None:
    if isinstance(item, str) and item.strip() != "":
        return {"type": "link", "url": item.strip()}
    if not isinstance(item, dict):
        return None

    return clone_json_object(item)


def _is_link_media(item: Mapping[str, JSONValue]) -> bool:
    media_type = item.get("type")
    if isinstance(media_type, str) and media_type.strip().lower() in {"link", "url"}:
        return True

    return "url" in item and "type" not in item


def _normalize_platform(platform: str) -> str:
    normalized = platform.strip().lower()
    if normalized == "":
        raise ValueError("platform не может быть пустой")
    return normalized
