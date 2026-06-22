from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import pytest

from libs.shared import (
    BaseAppConfig,
    ServiceTemplateConfig,
    create_base_app,
    setup_logging,
)

LoggerSnapshot = tuple[int, bool, bool, list[logging.Handler]]


@pytest.fixture(autouse=True)
def restore_logging_state() -> Iterator[None]:
    root = logging.getLogger()
    uvicorn = logging.getLogger("uvicorn")
    uvicorn_error = logging.getLogger("uvicorn.error")
    uvicorn_access = logging.getLogger("uvicorn.access")
    snapshots = {
        root.name: (root, _snapshot_logger(root)),
        uvicorn.name: (uvicorn, _snapshot_logger(uvicorn)),
        uvicorn_error.name: (uvicorn_error, _snapshot_logger(uvicorn_error)),
        uvicorn_access.name: (uvicorn_access, _snapshot_logger(uvicorn_access)),
    }

    yield

    for logger, snapshot in snapshots.values():
        _restore_logger(logger, snapshot)


def test_setup_logging_emits_parseable_json_to_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging("DEBUG", service_name="issue-221-service")
    logger = logging.getLogger("tests.issue221")

    logger.debug("structured event", extra={"tenant_id": "tenant-a", "attempt": 1})

    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload == {
        "attempt": 1,
        "level": "DEBUG",
        "logger": "tests.issue221",
        "message": "structured event",
        "service": "issue-221-service",
        "tenant_id": "tenant-a",
        "timestamp": payload["timestamp"],
    }


def test_setup_logging_uses_log_level_environment(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    setup_logging()
    logger = logging.getLogger("tests.issue221.env")

    logger.warning("filtered")
    logger.error("visible")

    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["message"] == "visible"


def test_setup_logging_disables_uvicorn_access_log_by_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging("INFO")
    access_logger = logging.getLogger("uvicorn.access")

    access_logger.info("GET /health 200")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert access_logger.disabled is True
    assert access_logger.handlers == []
    assert access_logger.propagate is False


def test_create_base_app_applies_json_logging_config(
    capsys: pytest.CaptureFixture[str],
) -> None:
    create_base_app(
        BaseAppConfig(
            service=ServiceTemplateConfig(
                service_name="issue-221-base-app",
                jwt_secret="test-only-jwt-secret",
            ),
            log_level="ERROR",
        )
    )
    logger = logging.getLogger("tests.issue221.base")

    logger.warning("filtered by base app")
    logger.error("base app error")

    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["level"] == "ERROR"
    assert payload["message"] == "base app error"
    assert payload["service"] == "issue-221-base-app"
    assert logging.getLogger("uvicorn.access").disabled is True


def _snapshot_logger(logger: logging.Logger) -> LoggerSnapshot:
    return (
        logger.level,
        logger.disabled,
        logger.propagate,
        list(logger.handlers),
    )


def _restore_logger(logger: logging.Logger, snapshot: LoggerSnapshot) -> None:
    level, disabled, propagate, handlers = snapshot
    logger.setLevel(level)
    logger.disabled = disabled
    logger.propagate = propagate
    logger.handlers = handlers
