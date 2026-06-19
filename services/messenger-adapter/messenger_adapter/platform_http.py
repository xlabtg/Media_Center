from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, cast

import httpx


def response_json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}

    if not isinstance(payload, dict):
        return {}

    return cast(dict[str, Any], payload)


def int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def retry_after_seconds(value: object, *, now: datetime | None = None) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return _positive_float(value)
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    if stripped == "":
        return None

    try:
        return _positive_float(float(stripped))
    except ValueError:
        pass

    try:
        retry_at = parsedate_to_datetime(stripped)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None

    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)

    resolved_now = now or datetime.now(UTC)
    return max((retry_at.astimezone(UTC) - resolved_now).total_seconds(), 0.0)


def _positive_float(value: int | float) -> float | None:
    resolved = float(value)
    if resolved < 0:
        return None
    return resolved
