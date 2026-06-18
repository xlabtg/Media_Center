from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from libs.shared import ServiceTemplateConfig, create_service_app, encode_hs256_jwt

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_service_template_directory_documents_scaffold_contract() -> None:
    expected_files = [
        "services/service-template/README.md",
        "services/service-template/.env.example",
        "services/service-template/alembic.ini",
        "services/service-template/app/__init__.py",
        "services/service-template/app/main.py",
        "services/service-template/app/settings.py",
        "services/service-template/migrations/README.md",
        "services/service-template/tests/test_health.py",
    ]

    missing = [path for path in expected_files if not (ROOT / path).is_file()]

    assert not missing

    readme = read_text("services/service-template/README.md")
    for marker in (
        "FastAPI",
        "create_service_app",
        "/health",
        "/metrics",
        "TenantContextASGIMiddleware",
        "DatabaseSettings",
        "alembic",
    ):
        assert marker in readme

    env_template = read_text("services/service-template/.env.example")
    for marker in (
        "SERVICE_NAME=service-template",
        "SERVICE_VERSION=0.1.0",
        "DATABASE_URL=postgresql+asyncpg://",
        "JWT_SECRET=",
        "PROMETHEUS_ENABLED=true",
    ):
        assert marker in env_template
    assert "CHANGE_ME" not in env_template


def test_service_template_app_exposes_health_metrics_and_tenant_context() -> None:
    jwt_secret = "unit-test-jwt-secret"
    config = ServiceTemplateConfig(
        service_name="service-template",
        version="0.1.0",
        database_url="postgresql+asyncpg://nmc:nmc_dev_password@localhost:5432/nmc",
        jwt_secret=jwt_secret,
        prometheus_enabled=True,
    )
    client = TestClient(create_service_app(config))

    health = client.get("/health")

    assert health.status_code == 200
    assert health.json() == {
        "service": "service-template",
        "version": "0.1.0",
        "status": "ok",
        "checks": {
            "database": "configured",
            "metrics": "enabled",
        },
    }

    unauthorized = client.get("/template/context")

    assert unauthorized.status_code == 401
    assert unauthorized.json()["error"]["code"] == "unauthorized"

    token = encode_hs256_jwt(
        {
            "tenant_id": "tenant-a",
            "sub": "member-1",
            "roles": ["member_full"],
        },
        jwt_secret,
    )
    context_response = client.get(
        "/template/context",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-Id": "tenant-a",
            "X-Correlation-Id": "corr-template-1",
        },
    )

    assert context_response.status_code == 200
    assert context_response.json() == {
        "tenant_id": "tenant-a",
        "subject": "member-1",
        "roles": ["member_full"],
        "correlation_id": "corr-template-1",
    }

    metrics = client.get("/metrics")

    assert metrics.status_code == 200
    assert metrics.headers["content-type"].startswith("text/plain")
    assert (
        'nmc_service_operations_total{operation="healthcheck",'
        'service="service-template",status="success",tenant_id="platform"} 1'
    ) in metrics.text
    assert (
        'nmc_service_operations_total{operation="template_context",'
        'service="service-template",status="success",tenant_id="tenant-a"} 1'
    ) in metrics.text
    assert "member-1" not in metrics.text
