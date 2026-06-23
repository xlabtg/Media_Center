from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "docs/case-studies/issue-213/metrics/competitive-metrics-matrix.md"
COMPETITIVE_ANALYSIS_PATH = (
    ROOT / "docs/case-studies/issue-213/04-competitive-analysis.md"
)
BUDGET_CONFIG_PATH = ROOT / "docs/operations/service-performance-budgets.json"
STAGE_9_ACCEPTANCE_PATH = ROOT / "docs/STAGE_9_ACCEPTANCE.md"
CASE_STUDY_README_PATH = ROOT / "docs/case-studies/issue-213/README.md"


def test_issue_253_matrix_tracks_axes_with_current_and_target_values() -> None:
    matrix = _read_text(MATRIX_PATH)
    competitive_analysis = _read_text(COMPETITIVE_ANALYSIS_PATH)
    budget_config = json.loads(_read_text(BUDGET_CONFIG_PATH))

    source_axis_markers = (
        "Размер образа",
        "Холодный старт",
        "Частота деплоев",
        "Lead time",
        "Change Failure Rate",
        "MTTR",
        "Supply chain",
        "Надёжность",
    )
    for marker in source_axis_markers:
        assert marker in competitive_analysis

    matrix_axis_markers = (
        "Размер образа",
        "Cold-start до `/ready`",
        "Deployment frequency",
        "Lead time for changes",
        "Change failure rate",
        "MTTR",
        "Supply-chain",
        "SLO доступности",
    )
    for marker in matrix_axis_markers:
        assert marker in matrix

    table_contract_markers = (
        "Текущее значение",
        "Целевое значение",
        "Источник текущего значения",
        "REQ-M4",
    )
    for marker in table_contract_markers:
        assert marker in matrix

    assert f"< {budget_config['metrics']['image_size']['budget_mb']} МБ" in matrix
    assert (
        f"<= {budget_config['metrics']['cold_start']['budget_ms'] // 1000} с" in matrix
    )
    for target in (
        ">= 1 деплой/день",
        "< 1 день",
        "< 5 %",
        "< 1 час",
        "SBOM + cosign + SLSA",
        "99,9 %",
    ):
        assert target in matrix

    for placeholder in ("TBD", "TODO", "заполнить позже"):
        assert placeholder not in matrix


def test_issue_253_matrix_is_bound_to_f1_f2_and_release_update_process() -> None:
    matrix = _read_text(MATRIX_PATH)

    required_refs = (
        "issue #253",
        "F1 / #251",
        "F2 / #252",
        "docs/case-studies/issue-213/04-competitive-analysis.md",
        "docs/case-studies/issue-213/metrics/dora-data-sources.md",
        "infra/observability/grafana/dashboards/dora.json",
        "infra/observability/prometheus/rules/dora-metrics.yml",
        "docs/operations/service-performance-budgets.json",
        ".github/scripts/check_service_performance_budget.py",
        ".github/workflows/build-service.yml",
    )
    for marker in required_refs:
        assert marker in matrix

    update_process_markers = (
        "## Процесс обновления по релизам",
        "release owner",
        "gh run view",
        "service-performance-*",
        "nmc:dora_deployment_frequency:deploys_per_day",
        "nmc:dora_lead_time:p75_seconds",
        "nmc:dora_change_failure_rate:ratio30d",
        "nmc:dora_mttr:avg_seconds",
        "infra/observability/slo-targets.json",
        "docs/STAGE_9_ACCEPTANCE.md",
    )
    for marker in update_process_markers:
        assert marker in matrix


def test_issue_253_acceptance_snapshot_and_index_reference_matrix() -> None:
    docs = "\n".join(
        [
            _read_text(STAGE_9_ACCEPTANCE_PATH),
            _read_text(CASE_STUDY_README_PATH),
        ],
    )

    for marker in (
        "F3 / #253",
        "competitive-metrics-matrix.md",
        "tests/test_competitive_metrics_matrix_issue253_contract.py",
    ):
        assert marker in docs


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")
