from pathlib import Path

import pytest

from libs.shared.testing import (
    TenantTestIdentity,
    assert_only_tenant_records,
    build_tenant_test_dataset,
)

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_testing_strategy_documents_pyramid_coverage_and_tenant_fixtures() -> None:
    strategy = read_text("docs/TESTING_STRATEGY.md")

    required_markers = [
        "# Стратегия тестирования НМЦ",
        "## 2. Пирамида тестирования",
        "Unit",
        "Integration",
        "E2E",
        "35 %",
        "70 %",
        "80 %",
        "tenant_id",
        "TenantTestIdentity",
        "build_tenant_test_dataset",
        "cross-tenant",
        "tenant_isolation_violation",
    ]
    missing = [marker for marker in required_markers if marker not in strategy]

    assert not missing


def test_ci_measures_coverage_and_uploads_report() -> None:
    workflow = read_text(".github/workflows/ci.yml")

    required_markers = [
        "pytest --cov=libs --cov=services",
        "--cov-report=term-missing:skip-covered",
        "--cov-report=xml:coverage.xml",
        "--cov-fail-under=35",
        "actions/upload-artifact@v7.0.1",
        "coverage.xml",
    ]
    missing = [marker for marker in required_markers if marker not in workflow]

    assert not missing


def test_dev_requirements_pin_pytest_cov() -> None:
    requirements = set(read_text("requirements-dev.txt").splitlines())

    assert "pytest-cov==7.1.0" in requirements


def test_tenant_test_dataset_keeps_records_isolated_by_tenant() -> None:
    dataset = build_tenant_test_dataset()

    assert isinstance(dataset.owner, TenantTestIdentity)
    assert dataset.owner.tenant_id == "tenant-a"
    assert dataset.foreign.tenant_id == "tenant-b"
    assert dataset.owner.context().tenant_id == "tenant-a"
    assert dataset.owner.headers()["x-tenant-id"] == "tenant-a"
    assert dataset.owner.jwt() != dataset.foreign.jwt()

    assert_only_tenant_records(dataset.owner_records, "tenant-a")
    assert_only_tenant_records(dataset.foreign_records, "tenant-b")

    with pytest.raises(AssertionError, match="cross-tenant"):
        assert_only_tenant_records(dataset.all_records, "tenant-a")


def test_strategy_is_linked_from_primary_developer_docs() -> None:
    readme = read_text("README.md")
    contributing = read_text("CONTRIBUTING.md")

    assert "docs/TESTING_STRATEGY.md" in readme
    assert "docs/TESTING_STRATEGY.md" in contributing
