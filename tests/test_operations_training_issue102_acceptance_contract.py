from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OPERATIONS_PATH = ROOT / "docs/OPERATIONS_MANUAL.md"
TRAINING_PATH = ROOT / "docs/TENANT_TRAINING_PROGRAM.md"
KNOWLEDGE_BASE_PATH = ROOT / "docs/KNOWLEDGE_BASE.md"
TRAINING_RECORD_PATH = ROOT / "docs/operations/tenant-training-record.json"


def test_issue102_operations_manual_covers_stage8_tenant_operations() -> None:
    manual = OPERATIONS_PATH.read_text(encoding="utf-8")

    _assert_markers(
        manual,
        [
            "# Эксплуатационная документация НМЦ",
            "Статус: ops-ready для issue #102",
            "этап 8",
            "операционный день",
            "tenant lifecycle",
            "RACI",
            "readiness checklist",
            "tenant_id",
            "no_pdn_no_secrets",
            "SRE_RUNBOOK.md",
            "DISASTER_RECOVERY.md",
            "MULTITENANT_SCALING.md",
            "TENANT_MARKETPLACE.md",
            "RL-KPI",
            "tests/test_operations_training_issue102_acceptance_contract.py",
        ],
    )


def test_issue102_training_program_and_record_confirm_completed_training() -> None:
    program = TRAINING_PATH.read_text(encoding="utf-8")
    record = _mapping(json.loads(TRAINING_RECORD_PATH.read_text(encoding="utf-8")))

    _assert_markers(
        program,
        [
            "# Программа обучения команд tenant'ов",
            "Статус: training-complete для issue #102",
            "администраторы tenant",
            "Совет",
            "Правление",
            "поддержка",
            "SRE/on-call",
            "контрольный сценарий",
            "knowledge check",
            "tenant-training-record.json",
        ],
    )

    assert record["issue"] == 102
    assert record["status"] == "training-complete"
    assert record["evidence_policy"] == "no_pdn_no_secrets"
    assert record["completed_at"] == "2026-06-20"
    assert record["knowledge_base"] == {
        "entrypoint": "docs/KNOWLEDGE_BASE.md",
        "update_cadence_days": 14,
    }

    required_roles = set(_sequence(record["required_roles"]))
    assert {
        "tenant-admin",
        "council",
        "board",
        "support",
        "sre-oncall",
    } <= required_roles

    sessions = [_mapping(session) for session in _sequence(record["sessions"])]
    tracks = {str(session["track"]) for session in sessions}
    assert {
        "tenant-admin",
        "council-governance",
        "support-triage",
        "sre-dr",
    } <= tracks

    for session in sessions:
        assert session["attendance_status"] == "completed"
        assert int(session["assessment_pass_rate_percent"]) >= 90
        assert session["evidence_policy"] == "no_pdn_no_secrets"
        assert _sequence(session["modules"])

    serialized = json.dumps(record, ensure_ascii=False)
    assert "@" not in serialized
    assert "token" not in serialized.lower()


def test_issue102_knowledge_base_is_available_and_has_update_workflow() -> None:
    knowledge_base = KNOWLEDGE_BASE_PATH.read_text(encoding="utf-8")

    _assert_markers(
        knowledge_base,
        [
            "# База знаний эксплуатации НМЦ",
            "Статус: kb-ready для issue #102",
            "## 1. Карта знаний",
            "## 2. Быстрые ответы",
            "## 3. Runbook update workflow",
            "## 4. Матрица владельцев",
            "## 5. Review cadence",
            "Запуск tenant",
            "Ежедневная эксплуатация",
            "P0/P1",
            "Backup/DR",
            "RL-KPI",
            "14 дней",
            "docs/OPERATIONS_MANUAL.md",
            "docs/TENANT_TRAINING_PROGRAM.md",
        ],
    )


def test_issue102_docs_are_published_from_primary_navigation() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    governance = (ROOT / "docs/GOVERNANCE.md").read_text(encoding="utf-8")
    sre_runbook = (ROOT / "docs/SRE_RUNBOOK.md").read_text(encoding="utf-8")
    retrospective = (ROOT / "docs/PILOT_RETROSPECTIVE_SCALE_PLAN.md").read_text(
        encoding="utf-8",
    )

    for marker in (
        "docs/OPERATIONS_MANUAL.md",
        "docs/TENANT_TRAINING_PROGRAM.md",
        "docs/KNOWLEDGE_BASE.md",
    ):
        assert marker in readme
        assert marker in retrospective

    assert "docs/TENANT_TRAINING_PROGRAM.md" in governance
    assert "docs/OPERATIONS_MANUAL.md" in sre_runbook
    assert "docs/KNOWLEDGE_BASE.md" in sre_runbook


def _assert_markers(content: str, markers: list[str]) -> None:
    missing = [marker for marker in markers if marker not in content]

    assert not missing


def _mapping(value: Any) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def _sequence(value: Any) -> list[Any]:
    assert isinstance(value, list)
    return value
