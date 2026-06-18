from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def assert_markers(path: str, markers: list[str]) -> None:
    content = read_text(path)
    missing = [marker for marker in markers if marker not in content]

    assert not missing


def test_issue_28_stage1_acceptance_snapshot_covers_epic_criteria() -> None:
    assert_markers(
        "docs/STAGE_1_ACCEPTANCE.md",
        [
            "Статус: acceptance snapshot для issue #28",
            "## 1. Решение по этапу 1",
            "## 2. Трассировка задач #16-#27",
            "## 3. Критерии завершения эпика #28",
            "Запрос с JWT проходит через Gateway с проверкой tenant_id",
            "Межтенантный доступ возвращает 403 tenant_isolation_violation",
            "Поднимается шаблон сервиса с метриками, миграциями и тестами",
            "## 4. Gate перед этапом 2",
            "## 5. Локальная проверка",
            "pytest",
        ],
    )


def test_issue_28_stage1_acceptance_links_all_child_artifacts() -> None:
    acceptance = read_text("docs/STAGE_1_ACCEPTANCE.md")

    for issue in range(16, 28):
        assert f"#{issue}" in acceptance

    for marker in (
        "libs/shared/tenant.py",
        "libs/shared/auth.py",
        "libs/shared/rbac.py",
        "libs/shared/gateway.py",
        "libs/shared/db.py",
        "libs/shared/cache.py",
        "libs/shared/events.py",
        "libs/shared/vector.py",
        "libs/shared/object_storage.py",
        "libs/shared/observability.py",
        "libs/shared/config.py",
        "libs/shared/service_template.py",
        "infra/db/alembic/versions/tenant_foundation_0001.py",
        "infra/observability/README.md",
        "services/service-template/README.md",
    ):
        assert marker in acceptance


def test_stage1_acceptance_is_discoverable_from_readme() -> None:
    assert "docs/STAGE_1_ACCEPTANCE.md" in read_text("README.md")
