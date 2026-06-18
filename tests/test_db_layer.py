from __future__ import annotations

import asyncio
import importlib
from collections.abc import Sequence
from typing import Protocol, TypeVar, cast

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect
from sqlalchemy.sql import Select

from libs.shared import (
    InMemoryAuditSink,
    TenantContext,
    TenantIsolationError,
    TenantScopedSQLAlchemyRepository,
    TenantSetting,
    create_async_engine_from_url,
    database_url_from_env,
)

ModelT = TypeVar("ModelT")


class _MigrationModule(Protocol):
    op: object

    def upgrade(self) -> None: ...

    def downgrade(self) -> None: ...


class _ScalarResult[ModelT]:
    def __init__(self, rows: Sequence[ModelT]) -> None:
        self._rows = tuple(rows)

    def all(self) -> list[ModelT]:
        return list(self._rows)


class _FakeAsyncSession[ModelT]:
    def __init__(self, rows: Sequence[ModelT]) -> None:
        self._rows = tuple(rows)
        self.last_statement: Select[tuple[ModelT]] | None = None

    async def scalars(
        self,
        statement: Select[tuple[ModelT]],
    ) -> _ScalarResult[ModelT]:
        self.last_statement = statement
        return _ScalarResult(self._rows)


def test_database_url_from_env_requires_asyncpg_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="DATABASE_URL"):
        database_url_from_env()

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://nmc:secret@localhost:5432/nmc",
    )

    assert database_url_from_env() == (
        "postgresql+asyncpg://nmc:secret@localhost:5432/nmc"
    )


def test_create_async_engine_from_url_requires_asyncpg_driver() -> None:
    with pytest.raises(ValueError, match="postgresql\\+asyncpg"):
        create_async_engine_from_url("postgresql://nmc:secret@localhost:5432/nmc")

    engine = create_async_engine_from_url(
        "postgresql+asyncpg://nmc:secret@localhost:5432/nmc",
    )
    try:
        assert engine.url.drivername == "postgresql+asyncpg"
    finally:
        asyncio.run(engine.dispose())


def test_repository_builds_tenant_filter_and_awaits_async_scalars() -> None:
    context = TenantContext(
        tenant_id="tenant-a",
        subject="member-1",
        roles=("member_full",),
        correlation_id="corr-db-1",
    )
    setting = TenantSetting(
        id="setting-1",
        tenant_id="tenant-a",
        key="feature.search",
        value_json={"enabled": True},
        version=1,
        updated_by="member-1",
    )
    session = _FakeAsyncSession([setting])
    repository = TenantScopedSQLAlchemyRepository(
        TenantSetting,
        resource_type="tenant_settings",
    )

    result = asyncio.run(repository.list_for_tenant(session, context))

    assert result == [setting]
    assert session.last_statement is not None
    compiled = str(
        session.last_statement.compile(compile_kwargs={"literal_binds": True})
    )
    assert "tenant_settings.tenant_id = 'tenant-a'" in compiled


def test_repository_denies_cross_tenant_records_with_audit_event() -> None:
    context = TenantContext(
        tenant_id="tenant-a",
        subject="member-1",
        roles=("member_full",),
        correlation_id="corr-db-2",
    )
    setting = TenantSetting(
        id="setting-2",
        tenant_id="tenant-b",
        key="feature.search",
        value_json={"enabled": True},
        version=1,
        updated_by="member-2",
    )
    audit_sink = InMemoryAuditSink()
    repository = TenantScopedSQLAlchemyRepository(
        TenantSetting,
        resource_type="tenant_settings",
    )

    with pytest.raises(TenantIsolationError) as exc_info:
        repository.require_owned(setting, context, audit_sink=audit_sink)

    assert exc_info.value.status_code == 403
    assert exc_info.value.error_code == "tenant_isolation_violation"
    assert len(audit_sink.events) == 1
    assert audit_sink.events[0].tenant_id == "tenant-a"
    assert audit_sink.events[0].requested_tenant_hash is not None
    assert audit_sink.events[0].correlation_id == "corr-db-2"


def test_contribution_ledger_models_build_tenant_scoped_queries() -> None:
    from libs.shared import Contribution, TenantWeight

    context = TenantContext(
        tenant_id="tenant-a",
        subject="member-1",
        roles=("member_full",),
        correlation_id="corr-db-3",
    )
    contribution_repository = TenantScopedSQLAlchemyRepository(
        Contribution,
        resource_type="contributions",
    )
    weight_repository = TenantScopedSQLAlchemyRepository(
        TenantWeight,
        resource_type="tenant_weights",
    )

    contribution_sql = str(
        contribution_repository.statement_for_tenant(context).compile(
            compile_kwargs={"literal_binds": True}
        )
    )
    weight_sql = str(
        weight_repository.statement_for_tenant(context).compile(
            compile_kwargs={"literal_binds": True}
        )
    )

    assert "contributions.tenant_id = 'tenant-a'" in contribution_sql
    assert "tenant_weights.tenant_id = 'tenant-a'" in weight_sql


def test_tenant_foundation_migration_upgrades_and_downgrades() -> None:
    migration_module = cast(
        _MigrationModule,
        importlib.import_module("infra.db.alembic.versions.tenant_foundation_0001"),
    )
    engine = create_engine("sqlite:///:memory:")

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        original_op = migration_module.op
        migration_module.op = operations
        try:
            migration_module.upgrade()
            upgraded_tables = set(inspect(connection).get_table_names())
            assert {"tenants", "tenant_settings"}.issubset(upgraded_tables)

            tenant_columns = {
                column["name"] for column in inspect(connection).get_columns("tenants")
            }
            assert {
                "tenant_id",
                "slug",
                "name",
                "status",
                "created_at",
                "updated_at",
            }.issubset(tenant_columns)

            migration_module.downgrade()
            downgraded_tables = set(inspect(connection).get_table_names())
            assert "tenant_settings" not in downgraded_tables
            assert "tenants" not in downgraded_tables
        finally:
            migration_module.op = original_op


def test_contribution_ledger_migration_upgrades_and_downgrades() -> None:
    foundation_module = cast(
        _MigrationModule,
        importlib.import_module("infra.db.alembic.versions.tenant_foundation_0001"),
    )
    ledger_module = cast(
        _MigrationModule,
        importlib.import_module("infra.db.alembic.versions.contribution_ledger_0002"),
    )
    engine = create_engine("sqlite:///:memory:")

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        original_foundation_op = foundation_module.op
        original_ledger_op = ledger_module.op
        foundation_module.op = operations
        ledger_module.op = operations
        try:
            foundation_module.upgrade()
            ledger_module.upgrade()
            inspector = inspect(connection)
            upgraded_tables = set(inspector.get_table_names())
            assert {"contributions", "tenant_weights"}.issubset(upgraded_tables)

            contribution_columns = {
                column["name"]: column
                for column in inspector.get_columns("contributions")
            }
            assert {
                "id",
                "tenant_id",
                "member_id",
                "event_type",
                "source_type",
                "source_ref",
                "points_awarded",
                "metadata",
                "audit_hash",
                "idempotency_key",
                "occurred_at",
                "created_at",
            }.issubset(contribution_columns)
            assert contribution_columns["tenant_id"]["nullable"] is False

            weight_columns = {
                column["name"]: column
                for column in inspector.get_columns("tenant_weights")
            }
            assert {
                "id",
                "tenant_id",
                "member_id",
                "period",
                "total_points",
                "avg_points_council",
                "kv_raw",
                "kv_capped",
                "payout_share",
                "calculation_hash",
                "calculated_at",
                "updated_at",
            }.issubset(weight_columns)
            assert weight_columns["tenant_id"]["nullable"] is False

            contribution_indexes = {
                index["name"] for index in inspector.get_indexes("contributions")
            }
            assert {
                "idx_contributions_tenant_id",
                "idx_contributions_tenant_event_created",
                "idx_contributions_tenant_member_occurred",
                "idx_contributions_tenant_source",
                "idx_contributions_audit_hash",
            }.issubset(contribution_indexes)

            weight_indexes = {
                index["name"] for index in inspector.get_indexes("tenant_weights")
            }
            assert {
                "idx_tenant_weights_tenant_id",
                "idx_tenant_weights_tenant_period",
                "idx_tenant_weights_tenant_period_kv",
                "idx_tenant_weights_calculation_hash",
            }.issubset(weight_indexes)

            contribution_uniques = {
                constraint["name"]
                for constraint in inspector.get_unique_constraints("contributions")
            }
            assert "uq_contributions_tenant_idempotency" in contribution_uniques

            weight_uniques = {
                constraint["name"]
                for constraint in inspector.get_unique_constraints("tenant_weights")
            }
            assert "uq_tenant_weights_tenant_member_period" in weight_uniques

            contribution_checks = {
                constraint["name"]
                for constraint in inspector.get_check_constraints("contributions")
            }
            assert "ck_contributions_points_non_negative" in contribution_checks

            weight_checks = {
                constraint["name"]
                for constraint in inspector.get_check_constraints("tenant_weights")
            }
            assert "ck_tenant_weights_kv_cap" in weight_checks

            ledger_module.downgrade()
            downgraded_tables = set(inspect(connection).get_table_names())
            assert "contributions" not in downgraded_tables
            assert "tenant_weights" not in downgraded_tables
            assert "tenants" in downgraded_tables
        finally:
            ledger_module.op = original_ledger_op
            foundation_module.op = original_foundation_op
