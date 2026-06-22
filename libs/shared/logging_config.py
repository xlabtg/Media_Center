from __future__ import annotations

import json
import logging
import math
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

LOG_LEVEL_ENV = "LOG_LEVEL"
DEFAULT_LOG_LEVEL = "INFO"
LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
UVICORN_LOGGER_NAMES = ("uvicorn", "uvicorn.error")
UVICORN_ACCESS_LOGGER_NAME = "uvicorn.access"

_RESERVED_LOG_RECORD_ATTRS = frozenset(
    logging.makeLogRecord({}).__dict__,
) | frozenset({"asctime", "message"})

type JSONLogValue = (
    None | bool | int | float | str | list["JSONLogValue"] | dict[str, "JSONLogValue"]
)


class JsonLogFormatter(logging.Formatter):
    def __init__(self, *, service_name: str | None = None) -> None:
        super().__init__()
        self._service_name = _normalize_optional_text(service_name)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, JSONLogValue] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if self._service_name is not None:
            payload["service"] = self._service_name

        for key, value in record.__dict__.items():
            if key in _RESERVED_LOG_RECORD_ATTRS or key in payload:
                continue
            payload[key] = _to_json_log_value(value)

        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info is not None:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def setup_logging(
    level: str | int | None = None,
    *,
    service_name: str | None = None,
    disable_uvicorn_access_log: bool = True,
) -> logging.Logger:
    normalized_level = normalize_log_level(level)
    root = logging.getLogger()
    root.handlers.clear()
    root.disabled = False
    root.setLevel(normalized_level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter(service_name=service_name))
    root.addHandler(handler)

    _configure_uvicorn_loggers(
        normalized_level=normalized_level,
        disable_access_log=disable_uvicorn_access_log,
    )
    return root


def normalize_log_level(level: str | int | None = None) -> str:
    raw_level: str | int
    if level is None:
        raw_level = os.environ.get(LOG_LEVEL_ENV, DEFAULT_LOG_LEVEL)
    else:
        raw_level = level

    if isinstance(raw_level, int):
        level_name = logging.getLevelName(raw_level)
        if isinstance(level_name, str) and level_name in LOG_LEVELS:
            return level_name
        raise ValueError(f"invalid log level: {raw_level}")

    normalized = raw_level.strip().upper()
    if normalized not in LOG_LEVELS:
        raise ValueError(f"invalid log level: {raw_level}")

    return normalized


def _configure_uvicorn_loggers(
    *,
    normalized_level: str,
    disable_access_log: bool,
) -> None:
    for logger_name in UVICORN_LOGGER_NAMES:
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.disabled = False
        logger.propagate = True
        logger.setLevel(normalized_level)

    access_logger = logging.getLogger(UVICORN_ACCESS_LOGGER_NAME)
    access_logger.handlers.clear()
    access_logger.setLevel(normalized_level)
    access_logger.disabled = disable_access_log
    access_logger.propagate = not disable_access_log


def _to_json_log_value(value: object) -> JSONLogValue:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _to_json_log_value(nested_value)
            for key, nested_value in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_to_json_log_value(item) for item in value]

    return str(value)


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = value.strip()
    if normalized == "":
        return None

    return normalized
