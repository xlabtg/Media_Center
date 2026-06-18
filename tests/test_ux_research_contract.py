from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def assert_markers(path: str, markers: list[str]) -> None:
    content = read_text(path)
    missing = [marker for marker in markers if marker not in content]

    assert not missing


def test_issue_13_ux_research_covers_scenarios_wireframes_and_design_system() -> None:
    assert_markers(
        "docs/UX_RESEARCH.md",
        [
            "Статус: baseline для issue #13",
            "## 2. Карта пользовательских сценариев",
            "Пайщик",
            "Совет",
            "Аудитория",
            "## 3. Информационная архитектура",
            "## 4. Wireframes v0",
            "### 4.1. Веб-кабинет пайщика",
            "### 4.2. Панель Совета",
            "## 5. Дизайн-система v0",
            "Цветовые токены",
            "Типографика",
            "Компоненты v0",
            "## 6. Трассировка к требованиям",
            "FR-11",
            "FR-09",
            "Human-in-the-Loop",
        ],
    )


def test_ux_research_is_linked_from_readme_navigation() -> None:
    assert "docs/UX_RESEARCH.md" in read_text("README.md")
