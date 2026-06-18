"""Создай таблицы Contribution Ledger.

Revision ID: 0002_contribution_ledger
Revises: 0001_tenant_foundation
Create Date: 2026-06-18 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_contribution_ledger"
down_revision: str | None = "0001_tenant_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contributions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("member_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_ref", sa.String(length=128), nullable=False),
        sa.Column("points_awarded", sa.Numeric(12, 2), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("audit_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.tenant_id"],
            name="fk_contributions_tenant_id_tenants",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_contributions"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_contributions_tenant_idempotency",
        ),
        sa.CheckConstraint(
            "points_awarded >= 0",
            name="ck_contributions_points_non_negative",
        ),
    )
    op.create_index("idx_contributions_tenant_id", "contributions", ["tenant_id"])
    op.create_index(
        "idx_contributions_tenant_event_created",
        "contributions",
        ["tenant_id", "event_type", "created_at"],
    )
    op.create_index(
        "idx_contributions_tenant_member_occurred",
        "contributions",
        ["tenant_id", "member_id", "occurred_at"],
    )
    op.create_index(
        "idx_contributions_tenant_source",
        "contributions",
        ["tenant_id", "source_type", "source_ref"],
    )
    op.create_index(
        "idx_contributions_audit_hash",
        "contributions",
        ["audit_hash"],
    )

    op.create_table(
        "tenant_weights",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("member_id", sa.String(length=36), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("total_points", sa.Numeric(14, 2), nullable=False),
        sa.Column("avg_points_council", sa.Numeric(14, 2), nullable=False),
        sa.Column("kv_raw", sa.Numeric(12, 6), nullable=False),
        sa.Column("kv_capped", sa.Numeric(6, 5), nullable=False),
        sa.Column("payout_share", sa.Numeric(12, 10), nullable=False),
        sa.Column("calculation_hash", sa.CHAR(length=64), nullable=False),
        sa.Column(
            "calculated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.tenant_id"],
            name="fk_tenant_weights_tenant_id_tenants",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tenant_weights"),
        sa.UniqueConstraint(
            "tenant_id",
            "member_id",
            "period",
            name="uq_tenant_weights_tenant_member_period",
        ),
        sa.CheckConstraint(
            "total_points >= 0 AND avg_points_council >= 0 AND "
            "kv_raw >= 0 AND kv_capped >= 0 AND payout_share >= 0",
            name="ck_tenant_weights_values_non_negative",
        ),
        sa.CheckConstraint("kv_capped <= 0.10", name="ck_tenant_weights_kv_cap"),
        sa.CheckConstraint("payout_share <= 1", name="ck_tenant_weights_payout_share"),
    )
    op.create_index("idx_tenant_weights_tenant_id", "tenant_weights", ["tenant_id"])
    op.create_index(
        "idx_tenant_weights_tenant_period",
        "tenant_weights",
        ["tenant_id", "period"],
    )
    op.create_index(
        "idx_tenant_weights_tenant_period_kv",
        "tenant_weights",
        ["tenant_id", "period", "kv_capped"],
    )
    op.create_index(
        "idx_tenant_weights_calculation_hash",
        "tenant_weights",
        ["calculation_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_tenant_weights_calculation_hash",
        table_name="tenant_weights",
    )
    op.drop_index(
        "idx_tenant_weights_tenant_period_kv",
        table_name="tenant_weights",
    )
    op.drop_index(
        "idx_tenant_weights_tenant_period",
        table_name="tenant_weights",
    )
    op.drop_index("idx_tenant_weights_tenant_id", table_name="tenant_weights")
    op.drop_table("tenant_weights")

    op.drop_index("idx_contributions_audit_hash", table_name="contributions")
    op.drop_index("idx_contributions_tenant_source", table_name="contributions")
    op.drop_index(
        "idx_contributions_tenant_member_occurred",
        table_name="contributions",
    )
    op.drop_index(
        "idx_contributions_tenant_event_created",
        table_name="contributions",
    )
    op.drop_index("idx_contributions_tenant_id", table_name="contributions")
    op.drop_table("contributions")
