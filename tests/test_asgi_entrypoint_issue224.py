from __future__ import annotations

import importlib
import sys
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest import MonkeyPatch


def test_contribution_ledger_app_uses_base_runtime_contract(
    monkeypatch: MonkeyPatch,
) -> None:
    module = _load_contribution_ledger_main(
        monkeypatch,
        {
            "APP_PORT": "7700",
            "JWT_SECRET": "test-only-jwt-secret",
        },
    )

    assert isinstance(module.app, FastAPI)
    assert module.app.state.base_app.config.app_port == 7700

    client = TestClient(module.app)

    info = client.get("/info")

    assert info.status_code == 200
    assert info.json()["service"] == "contribution-ledger"
    assert info.json()["port"] == 7700


def test_contribution_ledger_module_runner_uses_configured_host_and_port(
    monkeypatch: MonkeyPatch,
) -> None:
    module = _load_contribution_ledger_main(
        monkeypatch,
        {
            "APP_HOST": "127.0.0.1",
            "APP_PORT": "7701",
            "JWT_SECRET": "test-only-jwt-secret",
            "LOG_LEVEL": "warning",
        },
    )
    calls: list[dict[str, Any]] = []

    def fake_uvicorn_run(app: FastAPI, **kwargs: Any) -> None:
        calls.append({"app": app, **kwargs})

    monkeypatch.setattr(module.uvicorn, "run", fake_uvicorn_run)

    module.run()

    assert calls == [
        {
            "app": module.app,
            "host": "127.0.0.1",
            "port": 7701,
            "log_level": "warning",
        }
    ]


def _load_contribution_ledger_main(
    monkeypatch: MonkeyPatch,
    env: dict[str, str],
) -> Any:
    for name in ("APP_HOST", "APP_PORT", "JWT_SECRET", "LOG_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    sys.modules.pop("contribution_ledger_app.main", None)
    return importlib.import_module("contribution_ledger_app.main")
