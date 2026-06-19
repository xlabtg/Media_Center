"""Создай таблицу wallet operations.

Revision ID: 0004_wallet_operations
Revises: 0003_payout_distributions
Create Date: 2026-06-19 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_wallet_operations"
down_revision: str | None = "0003_payout_distributions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wallet_operations",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("member_id", sa.String(length=128), nullable=False),
        sa.Column("amount_mcv", sa.Numeric(14, 2), nullable=False),
        sa.Column("balance_after_mcv", sa.Numeric(14, 2), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("ref_type", sa.String(length=64), nullable=False),
        sa.Column("ref_id", sa.String(length=128), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=True),
        sa.Column("distribution_hash", sa.CHAR(length=64), nullable=True),
        sa.Column("payout_share", sa.Numeric(12, 10), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("audit_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
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
            name="fk_wallet_operations_tenant_id_tenants",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_wallet_operations"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_wallet_operations_tenant_idempotency",
        ),
        sa.CheckConstraint(
            "amount_mcv <> 0",
            name=op.f("ck_wallet_operations_amount_mcv_non_zero"),
        ),
        sa.CheckConstraint(
            "payout_share IS NULL OR (payout_share >= 0 AND payout_share <= 1)",
            name=op.f("ck_wallet_operations_payout_share_range"),
        ),
    )
    op.create_index(
        "idx_wallet_operations_tenant_id",
        "wallet_operations",
        ["tenant_id"],
    )
    op.create_index(
        "idx_wallet_operations_tenant_member_created",
        "wallet_operations",
        ["tenant_id", "member_id", "created_at"],
    )
    op.create_index(
        "idx_wallet_operations_tenant_ref",
        "wallet_operations",
        ["tenant_id", "ref_type", "ref_id"],
    )
    op.create_index(
        "idx_wallet_operations_audit_hash",
        "wallet_operations",
        ["audit_hash"],
    )


def downgrade() -> None:
    op.drop_index("idx_wallet_operations_audit_hash", table_name="wallet_operations")
    op.drop_index("idx_wallet_operations_tenant_ref", table_name="wallet_operations")
    op.drop_index(
        "idx_wallet_operations_tenant_member_created",
        table_name="wallet_operations",
    )
    op.drop_index("idx_wallet_operations_tenant_id", table_name="wallet_operations")
    op.drop_table("wallet_operations")
