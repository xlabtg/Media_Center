from __future__ import annotations

from fastapi.testclient import TestClient

from libs.shared import (
    DEFAULT_METRICS_PATH,
    DEFAULT_SERVICE_TEMPLATE_PUBLIC_PATHS,
    BaseAppConfig,
    ServiceTemplateConfig,
    TenantMetricRegistry,
    create_base_app,
)


def test_create_base_app_exposes_unified_prometheus_metrics_contract() -> None:
    registry = TenantMetricRegistry()
    app = create_base_app(
        BaseAppConfig(
            service=ServiceTemplateConfig(
                service_name="issue-220-metrics",
                jwt_secret="test-only-jwt-secret",
                prometheus_enabled=True,
                public_paths=(
                    *DEFAULT_SERVICE_TEMPLATE_PUBLIC_PATHS,
                    "/boom",
                ),
            )
        ),
        metrics=registry,
    )

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("boom")

    client = TestClient(app, raise_server_exceptions=False)

    assert DEFAULT_METRICS_PATH == "/metrics"
    assert DEFAULT_METRICS_PATH in app.openapi()["paths"]
    assert client.get("/info").status_code == 200
    assert client.get("/boom").status_code == 500

    metrics = client.get(DEFAULT_METRICS_PATH)

    assert metrics.status_code == 200
    assert metrics.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert "# TYPE nmc_service_operations_total counter" in metrics.text
    assert "# TYPE nmc_service_operation_duration_seconds histogram" in metrics.text
    assert (
        'nmc_service_operations_total{operation="http_request",'
        'service="issue-220-metrics",status="success",tenant_id="platform"} 1'
    ) in metrics.text
    assert (
        'nmc_service_operations_total{operation="http_request",'
        'service="issue-220-metrics",status="error",tenant_id="platform"} 1'
    ) in metrics.text
    assert (
        'nmc_service_operation_duration_seconds_count{operation="http_request",'
        'service="issue-220-metrics",status="success",tenant_id="platform"} 1'
    ) in metrics.text
    assert (
        'nmc_service_operation_duration_seconds_count{operation="http_request",'
        'service="issue-220-metrics",status="error",tenant_id="platform"} 1'
    ) in metrics.text
