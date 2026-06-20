from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "infra/local/fixtures/pilot-support-queue.json"
RUNBOOK_PATH = ROOT / "docs/PILOT_SUPPORT_RUNBOOK.md"
STAGE_DOC_PATH = ROOT / "docs/STAGE_7_ACCEPTANCE.md"


def test_issue94_support_queue_prioritizes_requests_and_critical_defects() -> None:
    fixture = _read_json(FIXTURE_PATH)

    tenant = _mapping(fixture["tenant"])
    channels = {
        channel["channel_id"]: _mapping(channel)
        for channel in _sequence(fixture["support_channels"])
    }
    severity_matrix = {
        rule["priority"]: _mapping(rule)
        for rule in _sequence(fixture["severity_matrix"])
    }
    cases = [_mapping(case) for case in _sequence(fixture["support_cases"])]

    assert tenant == {
        "slug": "nmc-pilot",
        "environment": "pilot",
        "data_policy": "synthetic_no_pdn",
    }
    assert {"support-intake", "security-privacy", "council-escalation"} <= set(channels)
    assert channels["support-intake"]["response_sla_hours"] == 4
    assert channels["security-privacy"]["response_sla_hours"] == 1
    assert channels["council-escalation"]["owner_role"] == "council"

    assert set(severity_matrix) == {"P0", "P1", "P2", "P3"}
    assert severity_matrix["P0"]["response_sla_hours"] <= 1
    assert severity_matrix["P0"]["fix_sla_hours"] <= 24
    assert severity_matrix["P0"]["requires_council_escalation"] is True
    assert severity_matrix["P0"]["requires_ci"] is True
    assert severity_matrix["P1"]["response_sla_hours"] <= 4
    assert severity_matrix["P1"]["fix_sla_hours"] <= 48
    assert severity_matrix["P1"]["requires_ci"] is True

    assert len(cases) >= 6
    assert any(case["priority"] == "P0" for case in cases)
    assert any(case["priority"] == "P1" for case in cases)
    assert any(case["category"] == "tenant_isolation" for case in cases)
    assert any(case["category"] == "onboarding" for case in cases)
    assert "@" not in json.dumps(cases, ensure_ascii=False)

    for case in cases:
        rule = severity_matrix[str(case["priority"])]
        opened_at = _parse_utc(str(case["opened_at"]))
        first_response_at = _parse_utc(str(case["first_response_at"]))
        elapsed_hours = (first_response_at - opened_at).total_seconds() / 3600

        assert case["tenant_slug"] == "nmc-pilot"
        assert case["status"] in {
            "triaged",
            "workaround_ready",
            "fix_ready",
            "resolved",
            "monitoring",
        }
        assert elapsed_hours <= int(rule["response_sla_hours"])
        assert case["evidence_policy"] == "no_pdn_no_secrets"
        if case["priority"] in {"P0", "P1"}:
            assert case["escalation_channel"] in channels
            assert case["ci_required"] is True


def test_issue94_bugfix_release_gate_requires_repro_test_ci_and_rollback() -> None:
    fixture = _read_json(FIXTURE_PATH)

    release_gate = _mapping(fixture["release_gate"])
    bugfixes = [_mapping(item) for item in _sequence(fixture["bugfixes"])]
    case_ids = {str(case["case_id"]) for case in _sequence(fixture["support_cases"])}

    assert release_gate["workflow"] == "CI"
    assert (
        release_gate["pull_request"]
        == "https://github.com/xlabtg/Media_Center/pull/194"
    )
    assert release_gate["required_local_checks"] == [
        "ruff check .",
        "ruff format --check .",
        "black --check .",
        "mypy .",
        "pytest",
        "bash experiments/validate_issue9_ci.sh",
    ]
    assert release_gate["required_ci_jobs"] == [
        "Lint, types, tests",
        "Security scan",
        "Build service image",
    ]

    assert bugfixes
    assert any(fix["priority"] == "P0" for fix in bugfixes)
    for fix in bugfixes:
        verification = _mapping(fix["verification"])
        assert fix["case_id"] in case_ids
        assert fix["status"] in {"fix_ready", "released", "monitoring"}
        assert verification["reproducing_test"].startswith("tests/")
        assert verification["ci_workflow"] == "CI"
        assert verification["rollback"]
        assert verification["post_release_monitoring_hours"] >= 24


def test_issue94_docs_publish_support_runbook_and_stage7_acceptance() -> None:
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
    stage = STAGE_DOC_PATH.read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    user_guide = (ROOT / "docs/USER_GUIDE.md").read_text(encoding="utf-8")
    council_guide = (ROOT / "docs/COUNCIL_GUIDE.md").read_text(encoding="utf-8")
    faq = (ROOT / "docs/FAQ.md").read_text(encoding="utf-8")

    for marker in (
        "# Runbook поддержки пилота и баг-фикс",
        "Статус: pilot-ready для issue #94",
        "`infra/local/fixtures/pilot-support-queue.json`",
        "Обращения обрабатываются в срок",
        "Критические дефекты устраняются приоритетно",
        "Изменения проходят через CI",
        "P0",
        "P1",
        "SLA",
        "tenant_isolation",
        "ruff check .",
        "pytest",
        "tests/test_pilot_support_issue94_acceptance_contract.py",
    ):
        assert marker in runbook

    for marker in (
        "Критерии приемки issue #94",
        "Поддержка и приём обращений",
        "Триаж и приоритизация дефектов",
        "Выпуск исправлений",
        "docs/PILOT_SUPPORT_RUNBOOK.md",
        "infra/local/fixtures/pilot-support-queue.json",
        "tests/test_pilot_support_issue94_acceptance_contract.py",
    ):
        assert marker in stage

    assert "docs/PILOT_SUPPORT_RUNBOOK.md" in readme
    assert "docs/PILOT_SUPPORT_RUNBOOK.md" in user_guide
    assert "docs/PILOT_SUPPORT_RUNBOOK.md" in council_guide
    assert "docs/PILOT_SUPPORT_RUNBOOK.md" in faq


def _read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def _mapping(value: Any) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def _sequence(value: Any) -> list[Any]:
    assert isinstance(value, list)
    return value


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(UTC)
