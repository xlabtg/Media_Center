from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def read_json(relative_path: str) -> dict[str, Any]:
    raw = json.loads(read_text(relative_path))
    assert isinstance(raw, dict)
    return raw


def assert_markers(path: str, markers: list[str]) -> None:
    content = read_text(path)
    missing = [marker for marker in markers if marker not in content]

    assert not missing


def test_issue96_stage7_acceptance_snapshot_covers_epic_criteria() -> None:
    assert_markers(
        "docs/STAGE_7_ACCEPTANCE.md",
        [
            "Статус: acceptance snapshot для issue #96",
            "## 1. Решение по этапу 7",
            "tenant `nmc-pilot` создан как `pilot_ready`",
            "15-25 участников",
            "Роли и пороги Совета заданы",
            "KPI пилота собираются",
            "доступны в отчёте Совету",
            "пользовательская документация опубликована",
            "поддержка пилота работает по SLA",
            "ретроспектива пилота проведена",
            "`approved_for_stage_8`",
            "rollback описан без удаления audit history",
            "## 7. Gate перед фактическим запуском",
            "## 8. Локальная проверка",
            "tests/test_stage7_acceptance_contract.py",
        ],
    )


def test_issue96_stage7_acceptance_links_all_child_artifacts() -> None:
    acceptance = read_text("docs/STAGE_7_ACCEPTANCE.md")

    for issue in range(91, 96):
        assert f"issue #{issue}" in acceptance

    for marker in (
        "infra/local/fixtures/pilot-tenant.json",
        "tests/test_pilot_tenant_issue91_acceptance_contract.py",
        "services/analytics-engine/README.md",
        "docs/modules/analytics-engine.md",
        "tests/test_pilot_kpi_telemetry_issue92_acceptance_contract.py",
        "docs/USER_GUIDE.md",
        "docs/COUNCIL_GUIDE.md",
        "docs/FAQ.md",
        "tests/test_user_docs_issue93_acceptance_contract.py",
        "docs/PILOT_SUPPORT_RUNBOOK.md",
        "infra/local/fixtures/pilot-support-queue.json",
        "tests/test_pilot_support_issue94_acceptance_contract.py",
        "docs/PILOT_RETROSPECTIVE_SCALE_PLAN.md",
        "tests/test_pilot_retrospective_issue95_acceptance_contract.py",
    ):
        assert marker in acceptance


def test_issue96_pilot_packet_matches_completion_criteria() -> None:
    tenant_packet = read_json("infra/local/fixtures/pilot-tenant.json")
    support_packet = read_json("infra/local/fixtures/pilot-support-queue.json")
    retrospective = read_text("docs/PILOT_RETROSPECTIVE_SCALE_PLAN.md")

    tenant = _mapping(tenant_packet["tenant"])
    participants = [_mapping(item) for item in _sequence(tenant_packet["participants"])]
    roles = Counter(participant["role"] for participant in participants)
    support_cases = [
        _mapping(item) for item in _sequence(support_packet["support_cases"])
    ]

    assert tenant["slug"] == "nmc-pilot"
    assert tenant["status"] == "pilot_ready"
    assert tenant["environment"] == "pilot"
    assert 15 <= len(participants) <= 25
    assert roles["council"] >= 3
    assert all(participant["status"] == "registered" for participant in participants)
    assert all(
        participant["onboarding_status"]
        in {"scheduled", "in_progress", "ready_for_review"}
        for participant in participants
    )

    assert any(case["priority"] == "P0" for case in support_cases)
    assert all(case["evidence_policy"] == "no_pdn_no_secrets" for case in support_cases)

    for marker in (
        "20 активных участников",
        "25 материалов",
        "12 500 просмотров",
        "4,5 минуты",
        "64 комментария",
        "12 задач",
        "1 инициатива",
        "`approved_for_stage_8`",
        "не является разрешением на production launch",
    ):
        assert marker in retrospective


def test_stage7_acceptance_is_discoverable_from_readme() -> None:
    readme = read_text("README.md")

    assert "docs/STAGE_7_ACCEPTANCE.md" in readme
    assert "Итоговая фиксация готовности пилотного запуска" in readme


def _mapping(value: Any) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def _sequence(value: Any) -> list[Any]:
    assert isinstance(value, list)
    return value
