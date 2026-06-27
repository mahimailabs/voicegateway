"""first-class tenants table with seeded default tenant

Revision ID: d5b8e3a1c9f2
Revises: 71683063682d
Create Date: 2026-06-27 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d5b8e3a1c9f2"
down_revision: str = "71683063682d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANTS_TABLE = sa.table(
    "tenants",
    sa.column("tenant_id", sa.String),
    sa.column("name", sa.String),
    sa.column("status", sa.String),
    sa.column("retention_days", sa.Integer),
    sa.column("branding_json", sa.String),
)


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column(
            "name",
            sa.String(),
            server_default="",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.String(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(),
            server_default="active",
            nullable=False,
        ),
        sa.Column("retention_days", sa.Integer(), nullable=True),
        sa.Column("branding_json", sa.String(), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'archived')",
            name="ck_tenants_status",
        ),
        sa.PrimaryKeyConstraint("tenant_id"),
    )
    op.create_index("idx_tenants_status", "tenants", ["status"], unique=False)

    op.bulk_insert(
        _TENANTS_TABLE,
        [
            {
                "tenant_id": "default",
                "name": "Default",
                "status": "active",
                "retention_days": None,
                "branding_json": None,
            }
        ],
    )


def downgrade() -> None:
    op.drop_index("idx_tenants_status", table_name="tenants")
    op.drop_table("tenants")
