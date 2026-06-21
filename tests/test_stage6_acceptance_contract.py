from __future__ import annotations

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def assert_markers(path: str, markers: list[str]) -> None:
    content = read_text(path)
    missing = [marker for marker in markers if marker not in content]

    assert not missing


def test_issue90_stage6_acceptance_snapshot_covers_epic_criteria() -> None:
    assert_markers(
        "docs/STAGE_6_ACCEPTANCE.md",
        [
            "Статус: acceptance snapshot для issue #90",
            "## 1. Решение по этапу 6",
            "качество, безопасность и производительность подтверждены",
            "coverage gate 35 %",
            "0 межтенантных утечек",
            "critical/high findings закрыты",
            "аудит ФЗ-152 пройден",
            "CGLR 100 req/s при p95 < 200 мс",
            "Contribution Ledger 50 событий/с",
            "Messenger 200 публикаций/мин",
            "HITL 10 очередей/ч",
            "полный цикл выплаты проходит e2e",
            "PostgreSQL, RabbitMQ, external API и proxy",
            "## 9. Gate перед пилотом",
            "## 10. Локальная проверка",
            "tests/test_stage6_acceptance_contract.py",
        ],
    )


def test_issue90_stage6_acceptance_links_all_child_artifacts() -> None:
    acceptance = read_text("docs/STAGE_6_ACCEPTANCE.md")

    for issue in range(83, 90):
        assert f"issue #{issue}" in acceptance

    for marker in (
        "docs/TESTING_STRATEGY.md",
        "tests/test_testing_strategy_issue83_contract.py",
        "tests/test_tenant_isolation_issue84_contract.py",
        "docs/modules/tenant-isolation.md",
        "docs/LOAD_TESTING.md",
        "tests/test_load_testing_issue85_acceptance_contract.py",
        "experiments/validate_issue85_load_targets.py",
        "docs/SECURITY.md",
        "docs/SECURITY_PENTEST_ISSUE_86.md",
        "tests/test_security_contract.py",
        "docs/COMPLIANCE.md",
        "tests/test_compliance_issue87_contract.py",
        "docs/modules/web-cabinet.md",
        "tests/test_hitl_payout_issue88_e2e_contract.py",
        "docs/modules/hitl-payout-gateway.md",
        "docs/CHAOS_TESTING.md",
        "libs/shared/resilience.py",
        "tests/test_chaos_resilience_issue89_contract.py",
    ):
        assert marker in acceptance


def test_issue90_stage6_underlying_artifacts_match_completion_criteria() -> None:
    testing_strategy = read_text("docs/TESTING_STRATEGY.md")
    load_testing = read_text("docs/LOAD_TESTING.md")
    tenant_isolation_doc = read_text("docs/modules/tenant-isolation.md")
    security_pentest = read_text("docs/SECURITY_PENTEST_ISSUE_86.md")
    compliance = read_text("docs/COMPLIANCE.md")
    chaos = read_text("docs/CHAOS_TESTING.md")

    assert "35 %" in testing_strategy
    assert "pytest --cov=libs --cov=services" in testing_strategy
    assert "tenant_id" in testing_strategy
    assert "403 tenant_isolation_violation" in tenant_isolation_doc

    assert "CGLR: 100 req/s при p95 < 200 мс" in load_testing
    assert "Contribution Ledger: 50 событий/с" in load_testing
    assert "Messenger: 200 публикаций/мин при > 99 % успеха" in load_testing
    assert "HITL: 10 очередей/ч, veto p95 < 5 с" in load_testing

    assert "F-86-01" in security_pentest
    assert "Severity: High" in security_pentest
    assert "Статус: повторная проверка пройдена" in security_pentest

    assert "Статус аудита #87: пройден" in compliance
    assert "DSAR workflow" in compliance
    assert "GET /compliance/fz152/checklist" in compliance

    assert "controlled_degradation" in chaos
    assert "recovery_confirmed" in chaos
    assert all(
        marker in chaos
        for marker in ("PostgreSQL", "RabbitMQ", "external API", "proxy")
    )


def test_issue90_ci_and_security_gates_remain_enabled() -> None:
    workflow = read_text(".github/workflows/ci.yml")

    for marker in (
        "ruff check .",
        "ruff format --check .",
        "mypy .",
        "pytest --cov=libs --cov=services",
        "--cov-fail-under=35",
        "pip-audit . --progress-spinner off",
        "gitleaks detect",
        "trivy-action",
    ):
        assert marker in workflow


def test_stage6_acceptance_is_discoverable_from_navigation() -> None:
    assert "docs/STAGE_6_ACCEPTANCE.md" in read_text("README.md")
    assert "docs/STAGE_6_ACCEPTANCE.md" in read_text("docs/ROADMAP.md")
    assert "docs/STAGE_6_ACCEPTANCE.md" in read_text("docs/MASTER_PLAN.md")


def test_issue90_load_target_helpers_are_declared_structurally() -> None:
    load_test = read_text("tests/test_load_testing_issue85_acceptance_contract.py")
    load_testing = read_text("libs/shared/load_testing.py")
    raw_workflow = read_text(".github/workflows/ci.yml")
    parsed_workflow = _yamlish_mapping(raw_workflow)

    assert "LoadTarget(" in load_test
    assert "LoadTestReport" in load_test
    assert "run_and_evaluate_load_scenario" in load_testing
    assert "run_async_and_evaluate_load_scenario" in load_testing
    assert parsed_workflow["name"] == "CI"


def _yamlish_mapping(raw: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for line in raw.splitlines():
        if not line or line.startswith(" ") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key] = value.strip()

    return result
