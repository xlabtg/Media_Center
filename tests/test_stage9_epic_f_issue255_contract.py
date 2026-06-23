from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_issue_255_epic_f_has_single_acceptance_snapshot() -> None:
    docs = "\n".join(
        [
            read_text("docs/STAGE_9_ACCEPTANCE.md"),
            read_text("docs/case-studies/issue-213/README.md"),
        ]
    )

    required_markers = [
        "issue #255",
        "Эпик F",
        "Операционное превосходство",
        "REQ-N1",
        "REQ-N2",
        "REQ-N3",
        "REQ-N5",
        "REQ-M4",
        "F1 / #251",
        "F2 / #252",
        "F3 / #253",
        "F4 / #254",
        "infra/observability/grafana/dashboards/dora.json",
        "infra/observability/prometheus/rules/dora-metrics.yml",
        "docs/operations/service-performance-budgets.json",
        ".github/scripts/check_service_performance_budget.py",
        ".github/workflows/build-service.yml",
        "docs/case-studies/issue-213/metrics/competitive-metrics-matrix.md",
        "infra/observability/slo-targets.json",
        "infra/observability/prometheus/rules/slo-error-budget.yml",
        "docs/operations/slo-error-budget.md",
        "tests/test_dora_grafana_issue251_contract.py",
        "tests/test_performance_budgets_issue252_contract.py",
        "tests/test_competitive_metrics_matrix_issue253_contract.py",
        "tests/test_slo_error_budget_issue254_contract.py",
        "tests/test_stage9_epic_f_issue255_contract.py",
    ]
    missing = [marker for marker in required_markers if marker not in docs]

    assert not missing


def test_issue_255_epic_f_keeps_operational_metrics_machine_checked() -> None:
    dashboard = _mapping(
        json.loads(read_text("infra/observability/grafana/dashboards/dora.json"))
    )
    budget_config = _mapping(
        json.loads(read_text("docs/operations/service-performance-budgets.json"))
    )
    slo_catalog = _mapping(
        json.loads(read_text("infra/observability/slo-targets.json"))
    )
    dora_rules = read_text("infra/observability/prometheus/rules/dora-metrics.yml")
    budget_workflow = yaml.safe_load(read_text(".github/workflows/build-service.yml"))
    matrix = read_text(
        "docs/case-studies/issue-213/metrics/competitive-metrics-matrix.md"
    )
    slo_rules = read_text("infra/observability/prometheus/rules/slo-error-budget.yml")

    panel_titles = {
        _mapping(panel)["title"] for panel in _sequence(dashboard["panels"])
    }
    assert {
        "Deployment frequency",
        "Lead time for changes",
        "Change failure rate",
        "MTTR",
    } <= panel_titles
    for record in (
        "nmc:dora_deployment_frequency:deploys_per_day",
        "nmc:dora_lead_time:p75_seconds",
        "nmc:dora_change_failure_rate:ratio30d",
        "nmc:dora_mttr:avg_seconds",
    ):
        assert record in dora_rules

    assert budget_config["metrics"]["image_size"]["budget_bytes"] == 250_000_000
    assert budget_config["metrics"]["cold_start"]["budget_ms"] == 3_000
    budget_steps = budget_workflow["jobs"]["build"]["steps"]
    budget_step_text = "\n".join(
        str(step.get("run", "")) for step in budget_steps if isinstance(step, dict)
    )
    assert "check_service_performance_budget.py" in budget_step_text
    assert "--report-dir performance-reports" in budget_step_text

    for marker in (
        "Текущее значение",
        "Целевое значение",
        "Источник текущего значения",
        "service-performance-*",
        "F1 / #251",
        "F2 / #252",
        "F4 / #254",
    ):
        assert marker in matrix

    slo_services = {
        _mapping(service)["service"] for service in _sequence(slo_catalog["services"])
    }
    assert {"api-gateway", "contribution-ledger", "wallet"} <= slo_services
    for marker in (
        "SloErrorBudgetFastBurn",
        "SloErrorBudgetSlowBurn",
        "SloLatencyP95Breached",
        "SloAvailabilityBreached",
        "runbook_url",
    ):
        assert marker in slo_rules


def _mapping(value: Any) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def _sequence(value: Any) -> list[Any]:
    assert isinstance(value, list)
    return value
