"""create workers table (fleet live roster)

Revision ID: e9b3d7c2a5f1
Revises: d5b8f2a14c7e
Create Date: 2026-07-04 12:00:00.000000

One row per fleet worker, keyed by ``(tenant_id, agent_id)``. Workers push a
heartbeat that upserts their row; the roster read derives an ``offline`` status
when the heartbeat ages past the TTL. The unique constraint on
``(tenant_id, agent_id)`` backs the upsert.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel  # noqa: F401 - migrations may reference SQLModel types

from alembic import op

revision: str = "e9b3d7c2a5f1"
down_revision: str | None = "d5b8f2a14c7e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("agent_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "project",
            sqlmodel.sql.sqltypes.AutoString(),
            server_default="default",
            nullable=False,
        ),
        sa.Column("tenant_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("region", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("version", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("host", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("active_sessions", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "status",
            sqlmodel.sql.sqltypes.AutoString(),
            server_default="idle",
            nullable=False,
        ),
        sa.Column("started_at", sa.Float(), nullable=True),
        sa.Column("last_seen", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "agent_id", name="uq_workers_tenant_agent"),
    )


def downgrade() -> None:
    op.drop_table("workers")
