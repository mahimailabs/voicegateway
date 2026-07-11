"""create managed_rate_rules table (DB rate-card overrides)

Revision ID: e1a4c8b6f2d9
Revises: c7d2a9f1e6b4
Create Date: 2026-07-11 12:00:00.000000

Persisted rate-card overrides layered on the YAML ``rate_card:`` seed. One row
per scope: ``rule_id`` is a deterministic key from (tenant, plan, modality,
provider, model), so setting the same scope again updates the row in place.
The gateway merges these after the seed rules when building the effective
RateCard.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel  # noqa: F401 - migrations may reference SQLModel types

from alembic import op

revision: str = "e1a4c8b6f2d9"
down_revision: str | None = "c7d2a9f1e6b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "managed_rate_rules",
        sa.Column("rule_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "modality",
            sqlmodel.sql.sqltypes.AutoString(),
            server_default="*",
            nullable=False,
        ),
        sa.Column(
            "provider",
            sqlmodel.sql.sqltypes.AutoString(),
            server_default="*",
            nullable=False,
        ),
        sa.Column(
            "model",
            sqlmodel.sql.sqltypes.AutoString(),
            server_default="*",
            nullable=False,
        ),
        sa.Column("tenant", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("plan", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column(
            "kind",
            sqlmodel.sql.sqltypes.AutoString(),
            server_default="cost_plus",
            nullable=False,
        ),
        sa.Column("markup", sa.Float(), nullable=True),
        sa.Column("unit_price_usd", sa.Float(), nullable=True),
        sa.Column("unit", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("rule_id"),
    )


def downgrade() -> None:
    op.drop_table("managed_rate_rules")
