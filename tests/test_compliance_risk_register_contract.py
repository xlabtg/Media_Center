from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def assert_markers(path: str, markers: list[str]) -> None:
    content = read_text(path)
    missing = [marker for marker in markers if marker not in content]

    assert not missing


def test_issue_14_risk_register_covers_assessed_risks_and_mitigations() -> None:
    assert_markers(
        "docs/RISK_REGISTER.md",
        [
            "Статус: baseline для issue #14",
            "## 1. Методика оценки",
            "Вероятность",
            "Влияние",
            "Остаточный риск",
            "## 2. Реестр рисков",
            "RR-TOS-01",
            "RR-PD-01",
            "RR-FIN-01",
            "RR-CONTENT-01",
            "Владелец риска",
            "Меры митигирования",
            "Срок/триггер",
            "## 3. Владельцы и cadence пересмотра",
            "## 4. Связь с pre-pilot gate",
        ],
    )


def test_issue_14_risk_register_is_linked_from_project_docs() -> None:
    for path in ("docs/COMPLIANCE.md", "README.md"):
        assert "docs/RISK_REGISTER.md" in read_text(path)
