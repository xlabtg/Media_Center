from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RETROSPECTIVE_PATH = ROOT / "docs/PILOT_RETROSPECTIVE_SCALE_PLAN.md"
STAGE_DOC_PATH = ROOT / "docs/STAGE_7_ACCEPTANCE.md"


def test_issue95_retrospective_documents_kpi_findings_and_council_approval() -> None:
    retrospective = RETROSPECTIVE_PATH.read_text(encoding="utf-8")

    _assert_markers(
        retrospective,
        [
            "# Ретроспектива пилота и план масштабирования",
            "Статус: scale-ready для issue #95",
            "`nmc-pilot`",
            "2026-W26",
            "Ретроспектива проведена и задокументирована",
            "Выводы согласованы с Советом",
            "План масштабирования утверждён",
            "`pilot-retro-2026-06-20`",
            "кворум 2/3",
            "`approved_for_stage_8`",
            "20 активных участников",
            "25 материалов",
            "12 500 просмотров",
            "4,5 минуты",
            "64 комментария",
            "12 задач",
            "1 инициатива",
            "без ПДн",
        ],
    )


def test_issue95_scale_plan_maps_stage8_workstreams_and_gates() -> None:
    retrospective = RETROSPECTIVE_PATH.read_text(encoding="utf-8")

    _assert_markers(
        retrospective,
        [
            "#97",
            "#98",
            "#99",
            "#100",
            "#101",
            "#102",
            "мультитенантное масштабирование",
            "SRE",
            "backup/DR",
            "каталог тенантов",
            "RL-KPI",
            "документация эксплуатации",
            "legal/security review",
            "vault",
            "HITL",
            "tenant_id",
            "rollback",
            "не является разрешением на production launch",
        ],
    )


def test_issue95_stage7_snapshot_and_navigation_publish_scale_plan() -> None:
    stage = STAGE_DOC_PATH.read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    council = (ROOT / "docs/COUNCIL_GUIDE.md").read_text(encoding="utf-8")
    support = (ROOT / "docs/PILOT_SUPPORT_RUNBOOK.md").read_text(encoding="utf-8")

    _assert_markers(
        stage,
        [
            "issue #95",
            "Критерии приемки issue #95",
            "Ретроспектива проведена и задокументирована",
            "Выводы согласованы с Советом",
            "План масштабирования утверждён",
            "docs/PILOT_RETROSPECTIVE_SCALE_PLAN.md",
            "tests/test_pilot_retrospective_issue95_acceptance_contract.py",
        ],
    )

    for content in (readme, council, support):
        assert "docs/PILOT_RETROSPECTIVE_SCALE_PLAN.md" in content


def _assert_markers(content: str, markers: list[str]) -> None:
    missing = [marker for marker in markers if marker not in content]

    assert not missing
