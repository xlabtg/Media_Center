from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from libs.shared import (
    BASE_APP_LOG_LEVELS,
    DEFAULT_BASE_APP_PORT,
    DEFAULT_METRICS_PATH,
    BaseAppConfig,
    S2SConfig,
    ServiceTemplateConfig,
    SharedSecretS2SAuth,
    create_base_app,
    setup_logging,
)

S2S_SECRET = "test-only-s2s-secret"

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


def test_issue_225_base_app_exposes_reference_runtime_contract() -> None:
    app = create_base_app(
        BaseAppConfig(
            service=ServiceTemplateConfig(
                service_name="issue-225-runtime",
                version="01.04.15",
                jwt_secret="test-only-jwt-secret",
                prometheus_enabled=True,
            ),
            build_metadata={
                "build_date": "2026-06-22T21:45:00Z",
                "git_commit": "abc123def456",
                "git_tag": "01.04.15",
                "python": "Python 3.13.0",
            },
            s2s=S2SConfig(shared_secret=S2S_SECRET),
        )
    )
    client = TestClient(app, raise_server_exceptions=False)

    assert app.state.base_app.config.app_port == DEFAULT_BASE_APP_PORT == 7700
    assert {
        "/health",
        "/ready",
        "/info",
        DEFAULT_METRICS_PATH,
        "/admin/log-level",
    } <= set(app.openapi()["paths"])

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {
        "service": "issue-225-runtime",
        "version": "01.04.15",
        "status": "ok",
        "checks": {},
    }

    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["checks"] == {
        "database": "not_configured",
        "redis": "not_configured",
        "broker": "not_configured",
        "metrics": "enabled",
    }

    info = client.get("/info")
    assert info.status_code == 200
    assert info.json()["service"] == "issue-225-runtime"
    assert info.json()["version"] == "01.04.15"
    assert info.json()["app_port"] == 7700
    assert info.json()["build"]["build_date"] == "2026-06-22T21:45:00Z"
    assert info.json()["build"]["git_commit"] == "abc123def456"

    metrics = client.get(DEFAULT_METRICS_PATH)
    assert metrics.status_code == 200
    assert metrics.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert "nmc_service_operations_total" in metrics.text

    log_level = client.put(
        "/admin/log-level",
        json={"level": "debug"},
        headers=_s2s_headers(method="PUT", nonce="issue-225-set-debug"),
    )
    assert log_level.status_code == 200
    assert log_level.json() == {"level": "DEBUG"}
    assert app.state.base_app.log_level == "DEBUG"
    assert logging.getLogger().level == logging.DEBUG

    current_log_level = client.get(
        "/admin/log-level",
        headers=_s2s_headers(method="GET", nonce="issue-225-get-debug"),
    )
    assert current_log_level.status_code == 200
    assert current_log_level.json() == {"level": "DEBUG"}


def test_issue_225_logging_uses_json_stdout_access_log_off_and_env_level(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOG_LEVEL", "error")

    root = setup_logging(service_name="issue-225-runtime")
    logger = logging.getLogger("tests.issue225")

    logger.warning("filtered")
    logger.error("visible", extra={"tenant_id": "platform"})

    captured = capsys.readouterr()
    lines = captured.out.splitlines()

    assert root.level == logging.ERROR
    assert "CRITICAL" in BASE_APP_LOG_LEVELS
    assert logging.getLogger("uvicorn.access").disabled is True
    assert captured.err == ""
    assert len(lines) == 1

    payload = json.loads(lines[0])
    assert payload == {
        "level": "ERROR",
        "logger": "tests.issue225",
        "message": "visible",
        "service": "issue-225-runtime",
        "tenant_id": "platform",
        "timestamp": payload["timestamp"],
    }


def _s2s_headers(*, method: str, nonce: str) -> dict[str, str]:
    signer = SharedSecretS2SAuth(S2SConfig(shared_secret=S2S_SECRET))
    return signer.sign_request(
        method=method,
        path="/admin/log-level",
        service_name="pytest",
        nonce=nonce,
    )


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
