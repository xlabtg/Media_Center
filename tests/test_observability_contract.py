from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from libs.shared import (
    ObservabilityContext,
    ObservabilityPrivacyError,
    TenantMetricRegistry,
    TenantTracer,
    build_structured_log_entry,
    format_structured_log,
)

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_prometheus_export_contains_required_tenant_labels() -> None:
    registry = TenantMetricRegistry()
    context = ObservabilityContext(
        tenant_id="tenant-a",
        service_name="contribution-ledger",
        operation="record_contribution",
        correlation_id="corr-obs-1",
    )

    registry.record_operation(
        context=context,
        status="success",
        duration_seconds=0.042,
    )

    metrics = registry.export_prometheus()

    assert "# TYPE nmc_service_operations_total counter" in metrics
    assert (
        'nmc_service_operations_total{operation="record_contribution",'
        'service="contribution-ledger",status="success",tenant_id="tenant-a"} 1'
    ) in metrics
    assert (
        "nmc_service_operation_duration_seconds_count{"
        'operation="record_contribution",service="contribution-ledger",'
        'status="success",tenant_id="tenant-a"} 1'
    ) in metrics
    assert "member_id" not in metrics
    assert "email" not in metrics


def test_structured_logs_include_tenant_and_reject_private_fields() -> None:
    context = ObservabilityContext(
        tenant_id="tenant-a",
        service_name="api-gateway",
        operation="route_request",
        correlation_id="corr-obs-2",
        trace_id="0af7651916cd43dd8448eb211c80319c",
        span_id="b7ad6b7169203331",
    )

    entry = build_structured_log_entry(
        level="info",
        message="request routed",
        context=context,
        payload={"status": "accepted", "actor_hash": "sha256:abc"},
        timestamp=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
    )
    raw_log = format_structured_log(entry)
    parsed = json.loads(raw_log)

    assert parsed["tenant_id"] == "tenant-a"
    assert parsed["correlation_id"] == "corr-obs-2"
    assert parsed["payload"] == {"actor_hash": "sha256:abc", "status": "accepted"}
    assert "@" not in raw_log
    assert "access_token" not in raw_log

    with pytest.raises(ObservabilityPrivacyError):
        build_structured_log_entry(
            level="info",
            message="unsafe payload",
            context=context,
            payload={
                "email": "member@example.test",
                "access_token": "redacted",
            },
        )


def test_trace_context_propagates_between_services_with_tenant_attributes() -> None:
    tracer = TenantTracer(id_generator=lambda length: "a" * length)
    context = ObservabilityContext(
        tenant_id="tenant-a",
        service_name="api-gateway",
        operation="proxy",
        correlation_id="corr-obs-3",
    )

    gateway_span = tracer.start_span("gateway.proxy", context=context)
    headers = gateway_span.propagation_headers()
    downstream_context = ObservabilityContext(
        tenant_id=headers["x-tenant-id"],
        service_name="contribution-ledger",
        operation="record_contribution",
        correlation_id=headers["x-correlation-id"],
    )
    service_span = tracer.start_span(
        "contribution-ledger.record",
        context=downstream_context,
        incoming_headers=headers,
    )

    assert headers["traceparent"].startswith("00-")
    assert service_span.trace_id == gateway_span.trace_id
    assert service_span.parent_span_id == gateway_span.span_id
    assert service_span.attributes["tenant_id"] == "tenant-a"
    assert service_span.attributes["correlation_id"] == "corr-obs-3"


def test_observability_infra_configs_and_docs_are_declared() -> None:
    compose = read_text("infra/local/docker-compose.yml")
    prometheus = read_text("infra/observability/prometheus/prometheus.yml")
    otel = read_text("infra/observability/otel-collector.yml")
    dashboard = read_text("infra/observability/grafana/dashboards/tenant-overview.json")
    docs = "\n".join(
        [
            read_text("README.md"),
            read_text("infra/README.md"),
            read_text("infra/observability/README.md"),
        ]
    )

    for marker in (
        "prometheus:",
        "image: prom/prometheus:v3.5.4",
        "grafana:",
        "image: grafana/grafana:12.4.4",
        "otel-collector:",
        "image: otel/opentelemetry-collector-contrib:0.154.0",
    ):
        assert marker in compose

    for marker in (
        "tenant_id",
        "nmc_service_operations_total",
        "nmc_service_operation_duration_seconds",
    ):
        assert marker in prometheus
        assert marker in dashboard

    assert "otlp" in otel
    assert "tenant_id" in otel
    assert "ПДн" in docs
    assert "OpenTelemetry" in docs
