from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def assert_markers(path: str, markers: list[str]) -> None:
    content = read_text(path)
    missing = [marker for marker in markers if marker not in content]

    assert not missing


def test_issue_15_stage0_acceptance_snapshot_covers_epic_criteria() -> None:
    assert_markers(
        "docs/STAGE_0_ACCEPTANCE.md",
        [
            "Статус: acceptance snapshot для issue #15",
            "## 1. Решение по этапу 0",
            "## 2. Трассировка задач #3-#14",
            "## 3. Критерии завершения эпика #15",
            "Утверждены ADR по архитектуре и технологическому стеку",
            "Работает CI/CD",
            "Поднимается локальная среда",
            "Согласованы модель данных, threat model, реестр рисков и глоссарий",
            "## 4. Gate перед реализацией",
            "## 5. Локальная проверка",
            "pytest",
        ],
    )


def test_issue_15_stage0_acceptance_links_all_child_artifacts() -> None:
    acceptance = read_text("docs/STAGE_0_ACCEPTANCE.md")

    for issue in range(3, 15):
        assert f"#{issue}" in acceptance

    for marker in (
        "docs/REQUIREMENTS.md",
        "docs/COMPLIANCE.md",
        "docs/ARCHITECTURE.md",
        "docs/adr/README.md",
        "docs/DATA_MODEL.md",
        "docs/SECURITY.md",
        "docs/RISK_REGISTER.md",
        "docs/UX_RESEARCH.md",
        ".github/workflows/ci.yml",
        "infra/local/docker-compose.yml",
        "CONTRIBUTING.md",
    ):
        assert marker in acceptance


def test_stage0_acceptance_is_discoverable_from_readme() -> None:
    assert "docs/STAGE_0_ACCEPTANCE.md" in read_text("README.md")
