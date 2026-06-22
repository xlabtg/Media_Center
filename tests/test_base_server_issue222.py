from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from libs.shared import BaseAppConfig, ServiceTemplateConfig, create_base_app
from libs.shared.s2s_auth import S2SConfig, SharedSecretS2SAuth

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


def test_log_level_put_accepts_json_body_and_updates_root_logger() -> None:
    client = TestClient(_app())
    logging.getLogger().setLevel(logging.INFO)

    response = client.put(
        "/admin/log-level",
        json={"level": "debug"},
        headers=_s2s_headers(method="PUT", nonce="set-debug"),
    )

    assert response.status_code == 200
    assert response.json() == {"level": "DEBUG"}
    assert logging.getLogger().level == logging.DEBUG

    current = client.get(
        "/admin/log-level",
        headers=_s2s_headers(method="GET", nonce="get-debug"),
    )

    assert current.status_code == 200
    assert current.json() == {"level": "DEBUG"}


def test_log_level_put_rejects_invalid_level_with_422() -> None:
    client = TestClient(_app())

    response = client.put(
        "/admin/log-level",
        json={"level": "TRACE"},
        headers=_s2s_headers(method="PUT", nonce="bad-level"),
    )

    assert response.status_code == 422


def test_log_level_put_requires_valid_s2s_signature() -> None:
    client = TestClient(_app())
    invalid_headers = _s2s_headers(method="PUT", nonce="invalid-signature")
    invalid_headers["X-S2S-Signature"] = "invalid"

    missing_signature = client.put("/admin/log-level", json={"level": "DEBUG"})
    invalid_signature = client.put(
        "/admin/log-level",
        json={"level": "DEBUG"},
        headers=invalid_headers,
    )

    assert missing_signature.status_code == 401
    assert invalid_signature.status_code == 401


def _app() -> FastAPI:
    return create_base_app(
        BaseAppConfig(
            service=ServiceTemplateConfig(
                service_name="issue-222-log-level",
                jwt_secret="test-only-jwt-secret",
            ),
            s2s=S2SConfig(shared_secret=S2S_SECRET),
        )
    )


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
