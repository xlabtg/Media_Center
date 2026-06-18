"""Создай tenant foundation таблицы.

Revision ID: 0001_tenant_foundation
Revises:
Create Date: 2026-06-18 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_tenant_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
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
        sa.PrimaryKeyConstraint("tenant_id", name="pk_tenants"),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
    )
    op.create_index("idx_tenants_status", "tenants", ["status"])

    op.create_table(
        "tenant_settings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.String(length=128), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.tenant_id"],
            name="fk_tenant_settings_tenant_id_tenants",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tenant_settings"),
        sa.UniqueConstraint(
            "tenant_id",
            "key",
            name="uq_tenant_settings_tenant_key",
        ),
    )
    op.create_index(
        "idx_tenant_settings_tenant_id",
        "tenant_settings",
        ["tenant_id"],
    )
    op.create_index(
        "idx_tenant_settings_tenant_updated",
        "tenant_settings",
        ["tenant_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_tenant_settings_tenant_updated",
        table_name="tenant_settings",
    )
    op.drop_index("idx_tenant_settings_tenant_id", table_name="tenant_settings")
    op.drop_table("tenant_settings")
    op.drop_index("idx_tenants_status", table_name="tenants")
    op.drop_table("tenants")
