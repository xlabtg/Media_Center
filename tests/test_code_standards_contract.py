from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def assert_markers(path: str, markers: list[str]) -> None:
    content = read_text(path)
    missing = [marker for marker in markers if marker not in content]

    assert not missing


def test_pre_commit_runs_local_quality_gate() -> None:
    assert_markers(
        ".pre-commit-config.yaml",
        [
            "repo: https://github.com/pre-commit/pre-commit-hooks",
            "id: check-yaml",
            "id: end-of-file-fixer",
            "repo: https://github.com/astral-sh/ruff-pre-commit",
            "id: ruff",
            "id: ruff-format",
            "repo: https://github.com/psf/black-pre-commit-mirror",
            "id: black",
            "repo: https://github.com/pre-commit/mirrors-mypy",
            "id: mypy",
            'args: ["--strict"]',
        ],
    )


def test_python_quality_tools_are_configured_and_pinned() -> None:
    pyproject = read_text("pyproject.toml")
    requirements = set(read_text("requirements-dev.txt").splitlines())

    for marker in (
        "[tool.ruff]",
        "[tool.ruff.lint]",
        "[tool.black]",
        "[tool.mypy]",
        'target-version = "py313"',
        "line-length = 88",
        "strict = true",
    ):
        assert marker in pyproject

    expected_requirements = {
        "ruff==0.15.17",
        "black==26.5.1",
        "mypy==2.1.0",
        "pre-commit==4.6.0",
    }

    assert expected_requirements.issubset(requirements)


def test_issue_and_pr_templates_cover_review_intake() -> None:
    template_files = {
        path.name for path in (ROOT / ".github" / "ISSUE_TEMPLATE").glob("*.yml")
    }

    assert {
        "bug.yml",
        "feature.yml",
        "task.yml",
        "research.yml",
        "epic.yml",
    }.issubset(template_files)

    assert_markers(
        ".github/pull_request_template.md",
        [
            "## Описание",
            "Closes #",
            "## Что сделано",
            "## Тестирование",
            "Локальный CI пройден",
            "black --check .",
            "pre-commit run --all-files",
            "## Чек-лист безопасности и комплаенса",
        ],
    )


def test_style_guide_is_published_and_linked() -> None:
    assert_markers(
        "docs/CODE_STYLE.md",
        [
            "# Гайд по стилю кода",
            "Python 3.13",
            "ruff check .",
            "ruff format --check .",
            "black --check .",
            "mypy .",
            "pre-commit install",
            "tenant_id",
            "Conventional Commits",
        ],
    )
    assert "docs/CODE_STYLE.md" in read_text("README.md")
    assert "docs/CODE_STYLE.md" in read_text("CONTRIBUTING.md")
