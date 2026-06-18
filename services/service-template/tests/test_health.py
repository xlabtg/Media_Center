from __future__ import annotations

from fastapi.testclient import TestClient

from libs.shared import ServiceTemplateConfig, create_service_app


def test_template_healthcheck_is_public() -> None:
    app = create_service_app(
        ServiceTemplateConfig(
            service_name="service-template",
            jwt_secret="test-only-jwt-secret",
            database_url="postgresql+asyncpg://nmc:nmc_dev_password@localhost:5432/nmc",
        )
    )
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
