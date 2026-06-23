from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]

PRODUCT_ENTRYPOINTS = (
    ("activity-command-center", "activity_command_center_app.main"),
    ("analytics-engine", "analytics_engine_app.main"),
    ("api-gateway", "api_gateway_app.main"),
    ("blockchain-auditor", "blockchain_auditor_app.main"),
    ("cglr", "cglr_app.main"),
    ("contribution-ledger", "contribution_ledger_app.main"),
    ("hitl-payout-gateway", "hitl_payout_gateway_app.main"),
    ("messenger-adapter", "messenger_adapter_app.main"),
    ("neuro-agent-orchestrator", "neuro_agent_orchestrator_app.main"),
    ("notification-gateway", "notification_gateway_app.main"),
    ("policy-manager", "policy_manager_app.main"),
    ("voice-to-chain", "voice_to_chain_app.main"),
    ("wallet", "wallet_app.main"),
    ("web-cabinet", "web_cabinet_app.main"),
)


@pytest.mark.parametrize(("service", "module_name"), PRODUCT_ENTRYPOINTS)
def test_issue_249_product_entrypoints_expose_unified_runtime_contract(
    monkeypatch: pytest.MonkeyPatch,
    service: str,
    module_name: str,
) -> None:
    _prepare_runtime_env(monkeypatch, service)
    module = _reload_module(module_name)
    app = module.app

    assert isinstance(app, FastAPI)
    assert app.state.base_app.config.service.service_name == service
    assert app.state.base_app.config.app_port == 7700

    client = TestClient(app, raise_server_exceptions=False)

    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200

    info = client.get("/info")
    assert info.status_code == 200
    assert info.json()["service"] == service
    assert info.json()["port"] == 7700

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "nmc_service_operations_total" in metrics.text


@pytest.mark.parametrize(("service", "module_name"), PRODUCT_ENTRYPOINTS)
def test_issue_249_product_entrypoints_support_python_module_run(
    service: str,
    module_name: str,
) -> None:
    package_name = module_name.split(".", maxsplit=1)[0]
    source = (ROOT / "services" / service / package_name / "main.py").read_text(
        encoding="utf-8"
    )

    assert "import uvicorn" in source
    assert "def run() -> None:" in source
    assert "uvicorn.run(" in source
    assert 'if __name__ == "__main__":' in source
    assert "run()" in source


def test_issue_249_stage9_acceptance_snapshot_documents_epic_e3() -> None:
    docs = "\n".join(
        [
            _read_text("docs/STAGE_9_ACCEPTANCE.md"),
            _read_text("services/README.md"),
        ]
    )

    required_markers = [
        "#249",
        "E3",
        "create_base_app",
        "python -m <service_app>.main",
        "tests/test_stage9_epic_e_issue249_contract.py",
        "/health",
        "/ready",
        "/info",
        "/metrics",
    ]
    missing = [marker for marker in required_markers if marker not in docs]

    assert not missing
    for service, module_name in PRODUCT_ENTRYPOINTS:
        assert service in docs
        assert module_name in docs


def _prepare_runtime_env(monkeypatch: pytest.MonkeyPatch, service: str) -> None:
    for name in (
        "APP_HOST",
        "APP_PORT",
        "BLOCKCHAIN_AUDITOR_URL",
        "DATABASE_URL",
        "HITL_TOTP_SECRET",
        "HITL_TOTP_SUBJECT",
        "HITL_TOTP_TENANT_ID",
        "JWT_SECRET",
        "LOG_LEVEL",
        "PROMETHEUS_ENABLED",
        "RABBITMQ_URL",
        "REDIS_URL",
        "RF_PAYMENT_GATEWAY_ENABLED",
        "SERVICE_NAME",
        "SERVICE_VERSION",
        "WHISPER_CPP_BINARY_PATH",
        "WHISPER_CPP_LANGUAGE",
        "WHISPER_CPP_MODEL_PATH",
        "WHISPER_CPP_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("APP_PORT", "7700")
    monkeypatch.setenv("JWT_SECRET", "test-only-jwt-secret")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("SERVICE_NAME", service)
    monkeypatch.setenv("SERVICE_VERSION", "0.1.0")


def _reload_module(module_name: str) -> Any:
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def _read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")
