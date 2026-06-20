from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK_PATH = ROOT / "docs/SRE_RUNBOOK.md"
SLO_PATH = ROOT / "infra/observability/slo-targets.json"
SRE_RULES_PATH = ROOT / "infra/observability/prometheus/rules/sre-alerts.yml"
ALERTMANAGER_PATH = ROOT / "infra/observability/alertmanager.yml"


def test_issue98_runbooks_publish_operational_incident_flows() -> None:
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")

    for marker in (
        "Статус: sre-ready для issue #98",
        "tests/test_sre_issue98_acceptance_contract.py",
        "tenant_isolation",
        "payout_halted",
        "publication_backlog",
        "private_blockchain_degraded",
        "observability_pipeline_down",
        "P0",
        "P1",
        "RACI",
        "postmortem",
        "no_pdn_no_secrets",
        "error budget",
        "Alertmanager",
        "Prometheus",
    ):
        assert marker in runbook

    assert "docs/SRE_RUNBOOK.md" in (ROOT / "README.md").read_text(
        encoding="utf-8",
    )
    assert "docs/SRE_RUNBOOK.md" in (ROOT / "infra/observability/README.md").read_text(
        encoding="utf-8",
    )


def test_issue98_slo_catalog_defines_sla_slo_and_monitoring_contracts() -> None:
    catalog = _mapping(json.loads(SLO_PATH.read_text(encoding="utf-8")))
    services = [_mapping(service) for service in _sequence(catalog["services"])]
    priorities = {
        item["priority"]: _mapping(item)
        for item in _sequence(catalog["incident_priorities"])
    }

    assert catalog["status"] == "sre-ready"
    assert catalog["issue"] == 98
    assert catalog["monitoring_stack"] == {
        "metrics": "Prometheus",
        "dashboards": "Grafana",
        "alerts": "Alertmanager",
        "traces": "OpenTelemetry",
    }
    assert {service["service"] for service in services} >= {
        "api-gateway",
        "contribution-ledger",
        "hitl-payout-gateway",
        "messenger-adapter",
        "blockchain-auditor",
        "observability",
    }

    for service in services:
        assert service["owner_role"]
        assert service["business_sla_percent"] >= 99
        assert service["availability_slo_percent"] >= 99
        assert service["latency_p95_ms"] > 0
        assert service["error_budget_percent"] == round(
            100 - float(service["availability_slo_percent"]),
            2,
        )
        assert service["metric_selectors"]["availability"].startswith(
            "nmc_service_operations_total",
        )
        assert "tenant_id" in service["metric_labels"]
        assert "service" in service["metric_labels"]
        assert "operation" in service["metric_labels"]
        assert "status" in service["metric_labels"]

    assert priorities["P0"]["ack_sla_minutes"] <= 15
    assert priorities["P0"]["mitigation_sla_minutes"] <= 60
    assert priorities["P0"]["requires_council_update"] is True
    assert priorities["P1"]["ack_sla_minutes"] <= 30
    assert priorities["P1"]["mitigation_sla_minutes"] <= 240


def test_issue98_prometheus_alerting_is_configured_and_tested() -> None:
    prometheus = (ROOT / "infra/observability/prometheus/prometheus.yml").read_text(
        encoding="utf-8",
    )
    compose = (ROOT / "infra/local/docker-compose.yml").read_text(encoding="utf-8")
    rules = SRE_RULES_PATH.read_text(encoding="utf-8")
    alertmanager = ALERTMANAGER_PATH.read_text(encoding="utf-8")

    assert "alertmanagers:" in prometheus
    assert "alertmanager:9093" in prometheus
    assert "alertmanager:" in compose
    assert "prom/alertmanager:" in compose
    assert (
        "../observability/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro"
        in (compose)
    )

    for alert_name in (
        "NmcSloAvailabilityBurnRateHigh",
        "NmcServiceLatencySloBreached",
        "NmcTenantOperationErrors",
        "NmcObservabilityPipelineDown",
        "NmcIncidentResponseSlaAtRisk",
    ):
        assert f"alert: {alert_name}" in rules

    for expression_marker in (
        "nmc:service_operations:rate5m",
        "nmc:service_operation_duration:p95_5m",
        "tenant_id",
        "runbook_url",
        "severity:",
        "team:",
    ):
        assert expression_marker in rules

    for route_marker in (
        "receiver: sre-oncall",
        "receiver: council-escalation",
        "receiver: security-privacy",
        'severity="critical"',
        'team="security"',
        "group_by: ['tenant_id', 'service', 'alertname']",
    ):
        assert route_marker in alertmanager

    assert _alert_count(rules) >= 5


def _alert_count(rules: str) -> int:
    return len(re.findall(r"^\s+- alert:", rules, flags=re.MULTILINE))


def _mapping(value: Any) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def _sequence(value: Any) -> list[Any]:
    assert isinstance(value, list)
    return value
