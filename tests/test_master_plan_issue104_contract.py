from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ISSUE_BASE = "https://github.com/xlabtg/Media_Center/issues"


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def read_json(relative_path: str) -> dict[str, int]:
    raw = json.loads(read_text(relative_path))
    assert isinstance(raw, dict)
    return {str(key): int(value) for key, value in raw.items()}


def load_plan() -> dict[str, Any]:
    spec = importlib.util.spec_from_file_location(
        "plan_data",
        ROOT / "experiments/plan_data.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    plan = module.PLAN
    assert isinstance(plan, dict)
    return plan


def flatten_nodes(node: dict[str, Any]) -> list[dict[str, Any]]:
    children = node.get("children", [])
    assert isinstance(children, list)
    nodes = [node]
    for child in children:
        assert isinstance(child, dict)
        nodes.extend(flatten_nodes(child))
    return nodes


def assert_markers(path: str, markers: list[str]) -> None:
    content = read_text(path)
    missing = [marker for marker in markers if marker not in content]

    assert not missing


def test_issue104_master_plan_snapshot_covers_completion_criteria() -> None:
    assert_markers(
        "docs/MASTER_PLAN.md",
        [
            "Статус: acceptance snapshot для issue #104",
            "## 1. Решение по мастер-плану",
            "## 2. Трассировка этапов #15-#103",
            "## 3. Контроль таксономии меток и milestones",
            "## 4. Критерии завершения issue #104",
            "Все этапы (0-8) заведены как milestones",
            "Каждая задача имеет метки type/priority/stage/area/component",
            "Документация (docs/*) согласована с задачами",
            "Команда может начать реализацию с этапа 0",
            "## 5. Локальная проверка",
            "tests/test_master_plan_issue104_contract.py",
        ],
    )


def test_issue104_master_plan_links_all_stage_epics_and_core_docs() -> None:
    master_plan = read_text("docs/MASTER_PLAN.md")

    for issue in (15, 28, 53, 66, 74, 82, 90, 96, 103):
        assert f"issue #{issue}" in master_plan
        assert f"{ISSUE_BASE}/{issue}" in master_plan

    for marker in (
        "docs/VISION.md",
        "docs/ARCHITECTURE.md",
        "docs/ROADMAP.md",
        "docs/ECONOMICS.md",
        "docs/GOVERNANCE.md",
        "docs/COMPLIANCE.md",
        "docs/SECURITY.md",
        "docs/GLOSSARY.md",
        "docs/DEVELOPMENT_PLAN.md",
    ):
        assert marker in master_plan


def test_issue104_plan_tree_matches_issue_map_and_development_plan() -> None:
    plan = load_plan()
    issue_map = read_json("experiments/issue_map.json")
    nodes = flatten_nodes(plan)
    development_plan = read_text("docs/DEVELOPMENT_PLAN.md")

    assert issue_map["M"] == 104
    assert len(nodes) == 102
    assert len(issue_map) == len(nodes)
    assert {str(node["key"]) for node in nodes} == set(issue_map)

    stage_epics = plan["children"]
    assert [stage["stage"] for stage in stage_epics] == list(range(9))
    assert [issue_map[str(stage["key"])] for stage in stage_epics] == [
        15,
        28,
        53,
        66,
        74,
        82,
        90,
        96,
        103,
    ]

    for node in nodes:
        issue_number = issue_map[str(node["key"])]
        assert f"/issues/{issue_number}" in development_plan


def test_issue104_master_plan_is_discoverable_from_readme_and_roadmap() -> None:
    assert "docs/MASTER_PLAN.md" in read_text("README.md")
    assert "docs/MASTER_PLAN.md" in read_text("docs/ROADMAP.md")
