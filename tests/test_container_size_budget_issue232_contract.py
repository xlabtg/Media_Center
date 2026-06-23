from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_issue_232_adr_accepts_image_size_optimization_budget() -> None:
    adr = read_text("docs/adr/0008-container-image-size-optimization.md")

    required_markers = [
        "# ADR-0008: Оптимизация размера сервисных образов",
        "**Статус:** Accepted",
        "**Связанный issue:** [#232]",
        "python:3.13.14-slim",
        "distroless",
        "REQ-1",
        "REQ-N1",
        "< 250 МБ",
        "stretch < 200 МБ",
        ".dockerignore",
        "F2",
        "docs/operations/image-size-budget.md",
    ]
    missing = [marker for marker in required_markers if marker not in adr]

    assert not missing


def test_issue_232_adr_index_lists_size_optimization_decision() -> None:
    index = read_text("docs/adr/README.md")

    assert "[ADR-0008](0008-container-image-size-optimization.md)" in index
    assert "Оптимизация размера сервисных образов" in index
    assert "Accepted" in index
    assert "2026-06-22" in index


def test_issue_232_image_size_budget_documents_baseline_measurement() -> None:
    budget = read_text("docs/operations/image-size-budget.md")

    required_markers = [
        "# Бюджет размера и cold-start сервисных образов",
        "REQ-N1",
        "REQ-N2",
        "Базовый бюджет",
        "< 250 МБ",
        "Stretch-бюджет",
        "< 200 МБ",
        "< 3 с",
        "docker image inspect",
        "media-center-contribution-ledger:issue232-baseline",
        "Базовый замер",
        "Дата замера",
        "2026-06-22",
    ]
    missing = [marker for marker in required_markers if marker not in budget]

    assert not missing


def test_issue_232_dockerignore_minimizes_service_image_context() -> None:
    dockerignore = read_text(".dockerignore")

    required_patterns = [
        ".git",
        ".github",
        ".venv",
        "__pycache__",
        "*.py[cod]",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".coverage",
        "coverage.xml",
        "htmlcov",
        ".env",
        ".env.*",
        "docs/",
        "experiments/",
        "examples/",
        "tests/",
        "ci-logs/",
        "logs/",
        "tmp/",
        "*.log",
        "*.mhtml",
    ]
    missing = [pattern for pattern in required_patterns if pattern not in dockerignore]

    assert not missing
    assert "!services/" not in dockerignore
    assert "!libs/" not in dockerignore
