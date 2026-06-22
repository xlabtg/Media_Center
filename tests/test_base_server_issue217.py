from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from libs.shared import (
    DEFAULT_BASE_APP_PORT,
    BaseAppConfig,
    ServiceTemplateConfig,
    create_base_app,
)


def test_create_base_app_registers_system_contract() -> None:
    config = BaseAppConfig(
        service=ServiceTemplateConfig(
            service_name="issue-217-service",
            version="1.2.3",
            jwt_secret="test-only-jwt-secret",
            prometheus_enabled=True,
        ),
        build_metadata={
            "build_date": "2026-06-22T00:00:00Z",
            "git_commit": "abc123",
        },
    )

    app = create_base_app(config)

    assert isinstance(app, FastAPI)
    assert app.state.service_template.config.service_name == "issue-217-service"
    assert app.state.base_app.config.app_port == DEFAULT_BASE_APP_PORT == 7700

    paths = set(app.openapi()["paths"])

    assert {"/health", "/ready", "/info", "/metrics", "/admin/log-level"} <= paths

    client = TestClient(app)

    assert client.get("/health").json() == {
        "service": "issue-217-service",
        "version": "1.2.3",
        "status": "ok",
        "checks": {},
    }

    ready = client.get("/ready")

    assert ready.status_code == 200
    assert ready.json() == {
        "service": "issue-217-service",
        "version": "1.2.3",
        "status": "ready",
        "checks": {
            "database": "not_configured",
            "redis": "not_configured",
            "broker": "not_configured",
            "metrics": "enabled",
        },
    }

    info = client.get("/info").json()

    assert info["service"] == "issue-217-service"
    assert info["version"] == "1.2.3"
    assert info["app_port"] == 7700
    assert info["build"]["build_date"] == "2026-06-22T00:00:00Z"
    assert info["build"]["git_commit"] == "abc123"

    metrics = client.get("/metrics")

    assert metrics.status_code == 200
    assert metrics.headers["content-type"].startswith("text/plain")


def test_create_base_app_keeps_docs_urls_configurable() -> None:
    config = BaseAppConfig(
        service=ServiceTemplateConfig(
            service_name="issue-217-docs",
            jwt_secret="test-only-jwt-secret",
        ),
        docs_url=None,
        redoc_url="/reference",
    )

    app = create_base_app(config)
    client = TestClient(app)

    assert client.get("/docs").status_code == 404
    assert client.get("/reference").status_code == 200


def test_create_base_app_accepts_service_template_config_directly() -> None:
    app = create_base_app(
        ServiceTemplateConfig(
            service_name="issue-217-direct",
            jwt_secret="test-only-jwt-secret",
        )
    )
    client = TestClient(app)

    assert client.get("/info").json()["service"] == "issue-217-direct"

    log_level = client.put("/admin/log-level", params={"level": "debug"})

    assert log_level.status_code == 200
    assert log_level.json() == {"level": "DEBUG"}
    assert client.get("/admin/log-level").json() == {"level": "DEBUG"}
