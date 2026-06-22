from __future__ import annotations

from fastapi.testclient import TestClient

from libs.shared import (
    ReadinessCheckResult,
    ServiceTemplateConfig,
    create_service_app,
)


def test_health_is_liveness_without_dependency_checks() -> None:
    app = create_service_app(
        ServiceTemplateConfig(
            service_name="issue-218-liveness",
            version="2.1.8",
            jwt_secret="test-only-jwt-secret",
            database_url="postgresql+asyncpg://nmc:nmc_dev_password@localhost:5432/nmc",
        )
    )
    client = TestClient(app)

    health = client.get("/health")

    assert health.status_code == 200
    assert health.json() == {
        "service": "issue-218-liveness",
        "version": "2.1.8",
        "status": "ok",
        "checks": {},
    }


def test_ready_returns_200_when_registered_checks_pass() -> None:
    app = create_service_app(
        ServiceTemplateConfig(
            service_name="issue-218-ready",
            version="2.1.8",
            jwt_secret="test-only-jwt-secret",
            prometheus_enabled=False,
        ),
        readiness_checks={
            "search-index": lambda: ReadinessCheckResult(
                status="available",
                ready=True,
            ),
        },
    )
    client = TestClient(app)

    ready = client.get("/ready")

    assert ready.status_code == 200
    assert ready.json() == {
        "service": "issue-218-ready",
        "version": "2.1.8",
        "status": "ready",
        "checks": {
            "database": "not_configured",
            "redis": "not_configured",
            "broker": "not_configured",
            "metrics": "disabled",
            "search-index": "available",
        },
    }


def test_ready_returns_503_when_registered_check_fails() -> None:
    app = create_service_app(
        ServiceTemplateConfig(
            service_name="issue-218-not-ready",
            version="2.1.8",
            jwt_secret="test-only-jwt-secret",
        ),
        readiness_checks={
            "broker": lambda: ReadinessCheckResult(
                status="unavailable",
                ready=False,
            ),
        },
    )
    client = TestClient(app)

    ready = client.get("/ready")

    assert ready.status_code == 503
    assert ready.json() == {
        "service": "issue-218-not-ready",
        "version": "2.1.8",
        "status": "not_ready",
        "checks": {
            "database": "not_configured",
            "redis": "not_configured",
            "broker": "unavailable",
            "metrics": "enabled",
        },
    }
