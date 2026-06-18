"""Создай таблицу payout distributions.

Revision ID: 0003_payout_distributions
Revises: 0002_contribution_ledger
Create Date: 2026-06-18 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_payout_distributions"
down_revision: str | None = "0002_contribution_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payout_distributions",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'ready'"),
            nullable=False,
        ),
        sa.Column("total_kv_capped", sa.Numeric(14, 5), nullable=False),
        sa.Column("total_payout_share", sa.Numeric(12, 10), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("distribution_json", sa.JSON(), nullable=False),
        sa.Column("distribution_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.tenant_id"],
            name="fk_payout_distributions_tenant_id_tenants",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_payout_distributions"),
        sa.UniqueConstraint(
            "tenant_id",
            "distribution_hash",
            name="uq_payout_distributions_tenant_hash",
        ),
        sa.CheckConstraint(
            "total_kv_capped >= 0 AND total_payout_share >= 0 AND member_count >= 0",
            name=op.f("ck_payout_distributions_values_non_negative"),
        ),
        sa.CheckConstraint(
            "total_payout_share <= 1",
            name=op.f("ck_payout_distributions_payout_share"),
        ),
    )
    op.create_index(
        "idx_payout_distributions_tenant_id",
        "payout_distributions",
        ["tenant_id"],
    )
    op.create_index(
        "idx_payout_distributions_tenant_period",
        "payout_distributions",
        ["tenant_id", "period"],
    )
    op.create_index(
        "idx_payout_distributions_tenant_status",
        "payout_distributions",
        ["tenant_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_payout_distributions_tenant_status",
        table_name="payout_distributions",
    )
    op.drop_index(
        "idx_payout_distributions_tenant_period",
        table_name="payout_distributions",
    )
    op.drop_index(
        "idx_payout_distributions_tenant_id",
        table_name="payout_distributions",
    )
    op.drop_table("payout_distributions")
