from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
SLO_CATALOG_PATH = ROOT / "infra/observability/slo-targets.json"
SLO_RULES_PATH = ROOT / "infra/observability/prometheus/rules/slo-error-budget.yml"
SLO_DOC_PATH = ROOT / "docs/operations/slo-error-budget.md"
KEY_SERVICE_TARGETS = {
    "api-gateway": {
        "availability_slo_percent": 99.9,
        "latency_p95_ms": 250,
        "error_budget_percent": 0.1,
        "error_budget_fraction": "0.001",
        "latency_seconds": "0.25",
    },
    "contribution-ledger": {
        "availability_slo_percent": 99.5,
        "latency_p95_ms": 500,
        "error_budget_percent": 0.5,
        "error_budget_fraction": "0.005",
        "latency_seconds": "0.5",
    },
    "wallet": {
        "availability_slo_percent": 99.5,
        "latency_p95_ms": 500,
        "error_budget_percent": 0.5,
        "error_budget_fraction": "0.005",
        "latency_seconds": "0.5",
    },
}


def test_issue_254_slo_catalog_defines_key_service_sli_slo_targets() -> None:
    catalog = _mapping(json.loads(SLO_CATALOG_PATH.read_text(encoding="utf-8")))
    services = {
        _mapping(service)["service"]: _mapping(service)
        for service in _sequence(catalog["services"])
    }

    assert catalog["req"] == "REQ-N5"
    assert catalog["stage9_issue"] == 254

    for service_name, expected in KEY_SERVICE_TARGETS.items():
        service = services[service_name]

        assert (
            service["availability_slo_percent"] == expected["availability_slo_percent"]
        )
        assert service["latency_p95_ms"] == expected["latency_p95_ms"]
        assert service["error_budget_percent"] == expected["error_budget_percent"]
        assert service["slo_window_days"] == 30
        assert service["metric_labels"] == [
            "tenant_id",
            "service",
            "operation",
            "status",
        ]
        assert service["metric_selectors"] == {
            "availability": (
                f'nmc_service_operations_total{{service="{service_name}"}}'
            ),
            "latency": (
                "nmc_service_operation_duration_seconds_bucket"
                f'{{service="{service_name}"}}'
            ),
            "errors": (
                "nmc_service_operations_total"
                f'{{service="{service_name}",status=~"error|denied"}}'
            ),
        }
        assert service["burn_rate_alerts"] == {
            "fast": {
                "short_window": "5m",
                "long_window": "1h",
                "burn_rate_threshold": 14.4,
                "severity": "critical",
            },
            "slow": {
                "short_window": "30m",
                "long_window": "6h",
                "burn_rate_threshold": 6.0,
                "severity": "warning",
            },
        }


def test_issue_254_prometheus_rules_alert_on_error_budget_burn_per_service() -> None:
    rules_text = SLO_RULES_PATH.read_text(encoding="utf-8")
    rules = _mapping(yaml.safe_load(rules_text))
    group_names = {_mapping(group)["name"] for group in _sequence(rules["groups"])}

    assert "nmc-slo-error-budget" in group_names
    for record in (
        "record: nmc:slo_error_ratio:ratio5m",
        "record: nmc:slo_error_ratio:ratio30m",
        "record: nmc:slo_error_ratio:ratio1h",
        "record: nmc:slo_error_ratio:ratio6h",
        "record: nmc:slo_error_budget_burn_rate:ratio5m",
        "record: nmc:slo_error_budget_burn_rate:ratio30m",
        "record: nmc:slo_error_budget_burn_rate:ratio1h",
        "record: nmc:slo_error_budget_burn_rate:ratio6h",
    ):
        assert record in rules_text

    for service_name, expected in KEY_SERVICE_TARGETS.items():
        alert_prefix = "".join(part.title() for part in service_name.split("-"))

        for alert_suffix in (
            "SloErrorBudgetFastBurn",
            "SloErrorBudgetSlowBurn",
            "SloLatencyP95Breached",
            "SloAvailabilityBreached",
        ):
            assert f"alert: Nmc{alert_prefix}{alert_suffix}" in rules_text

        error_budget_fraction = str(expected["error_budget_fraction"])
        latency_seconds = str(expected["latency_seconds"])
        assert f'service="{service_name}"' in rules_text
        assert error_budget_fraction in rules_text
        assert latency_seconds in rules_text

    for marker in (
        "burn_rate_alerts",
        "nmc:slo_error_budget_burn_rate:ratio5m",
        "nmc:slo_error_budget_burn_rate:ratio1h",
        "nmc:slo_error_budget_burn_rate:ratio30m",
        "nmc:slo_error_budget_burn_rate:ratio6h",
        "> 14.4",
        "> 6",
        "severity: critical",
        "severity: warning",
        "alert_type: slo_error_budget",
        "runbook_url",
    ):
        assert marker in rules_text


def test_issue_254_slo_docs_and_stage9_snapshot_are_linked() -> None:
    docs = "\n".join(
        [
            SLO_DOC_PATH.read_text(encoding="utf-8"),
            (ROOT / "docs/SRE_RUNBOOK.md").read_text(encoding="utf-8"),
            (ROOT / "docs/STAGE_9_ACCEPTANCE.md").read_text(encoding="utf-8"),
            (ROOT / "infra/observability/README.md").read_text(encoding="utf-8"),
        ],
    )

    for marker in (
        "issue #254",
        "REQ-N5",
        "api-gateway",
        "contribution-ledger",
        "wallet",
        "infra/observability/slo-targets.json",
        "infra/observability/prometheus/rules/slo-error-budget.yml",
        "Alertmanager",
        "error budget",
        "burn rate",
        "tests/test_slo_error_budget_issue254_contract.py",
        "F4 / #254",
    ):
        assert marker in docs


def _mapping(value: Any) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def _sequence(value: Any) -> list[Any]:
    assert isinstance(value, list)
    return value
