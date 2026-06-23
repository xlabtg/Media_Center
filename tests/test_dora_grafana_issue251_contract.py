from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_PATH = ROOT / "infra/observability/grafana/dashboards/dora.json"
RULES_PATH = ROOT / "infra/observability/prometheus/rules/dora-metrics.yml"
DATA_SOURCES_PATH = ROOT / "docs/case-studies/issue-213/metrics/dora-data-sources.md"


def test_issue_251_dora_dashboard_is_provisioned_with_four_metrics() -> None:
    datasource = _read_text(
        ROOT / "infra/observability/grafana/provisioning/datasources/prometheus.yml",
    )
    provisioning = _read_text(
        ROOT / "infra/observability/grafana/provisioning/dashboards/dashboards.yml",
    )
    compose = _read_text(ROOT / "infra/local/docker-compose.yml")
    dashboard = _mapping(json.loads(_read_text(DASHBOARD_PATH)))

    assert "uid: Prometheus" in datasource
    assert "/var/lib/grafana/dashboards" in provisioning
    assert "../observability/grafana/dashboards:/var/lib/grafana/dashboards:ro" in (
        compose
    )
    assert dashboard["uid"] == "nmc-dora"
    assert dashboard["title"] == "НМЦ / DORA"
    assert {"dora", "REQ-N3", "stage-9"} <= set(_strings(dashboard["tags"]))

    panels = {
        _mapping(panel)["title"]: _mapping(panel)
        for panel in _sequence(dashboard["panels"])
    }
    expected_panels = {
        "Deployment frequency": (
            "nmc:dora_deployment_frequency:deploys_per_day",
            "Цель: >= 1 деплой/день",
        ),
        "Lead time for changes": (
            "nmc:dora_lead_time:p75_seconds",
            "Цель: < 1 день",
        ),
        "Change failure rate": (
            "nmc:dora_change_failure_rate:ratio30d",
            "Цель: < 5%",
        ),
        "MTTR": (
            "nmc:dora_mttr:avg_seconds",
            "Цель: < 1 час",
        ),
    }

    assert expected_panels.keys() <= panels.keys()
    for title, markers in expected_panels.items():
        panel = panels[title]
        assert _mapping(panel["datasource"])["uid"] == "Prometheus"
        panel_text = json.dumps(panel, ensure_ascii=False)
        for marker in markers:
            assert marker in panel_text

    variable_names = {
        _mapping(variable)["name"]
        for variable in _sequence(_mapping(dashboard["templating"])["list"])
    }
    assert {"environment", "service"} <= variable_names


def test_issue_251_prometheus_rules_define_dora_metric_contract() -> None:
    rules = _read_text(RULES_PATH)

    for record in (
        "record: nmc:dora_deployment_frequency:deploys_per_day",
        "record: nmc:dora_lead_time:p75_seconds",
        "record: nmc:dora_change_failure_rate:ratio30d",
        "record: nmc:dora_mttr:avg_seconds",
    ):
        assert record in rules

    for source_metric in (
        "nmc_delivery_deployments_total",
        "nmc_delivery_lead_time_seconds_bucket",
        "nmc_delivery_changes_total",
        "nmc_incident_recovery_seconds_sum",
        "nmc_incident_recovery_seconds_count",
    ):
        assert source_metric in rules

    assert "rate(" in rules
    assert "histogram_quantile(0.75" in rules
    assert "clamp_min" in rules


def test_issue_251_dora_sources_are_documented() -> None:
    docs = "\n".join(
        [
            _read_text(DATA_SOURCES_PATH),
            _read_text(ROOT / "infra/observability/README.md"),
            _read_text(ROOT / "docs/STAGE_9_ACCEPTANCE.md"),
        ],
    )

    for marker in (
        "issue #251",
        "REQ-N3",
        "GitHub Actions",
        "GitHub Deployments",
        "incident",
        "nmc_delivery_deployments_total",
        "nmc_delivery_lead_time_seconds_bucket",
        "nmc_delivery_changes_total",
        "nmc_incident_recovery_seconds",
        "Deployment frequency",
        "Lead time for changes",
        "Change failure rate",
        "MTTR",
        "docs/case-studies/issue-213/metrics/dora-data-sources.md",
        "infra/observability/grafana/dashboards/dora.json",
        "tests/test_dora_grafana_issue251_contract.py",
    ):
        assert marker in docs


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _mapping(value: Any) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def _sequence(value: Any) -> list[Any]:
    assert isinstance(value, list)
    return value


def _strings(value: Any) -> list[str]:
    values = _sequence(value)
    assert all(isinstance(item, str) for item in values)
    return values
