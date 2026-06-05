"""create agent_observations rollup table (Phase 3 fleet rollup)

Revision ID: a3f9c1d7b2e8
Revises: c4f1a9e2b8d7
Create Date: 2026-06-04 12:00:00.000000

Phase 3 operational hardening: a windowed per-agent rollup so GET /api/agents
serves the fleet list from a pre-aggregated table instead of scanning every
latency row (the PR #33 deferred scan). The ``agent_id IS NULL`` row is the
unattributed bucket. The roll-up worker refreshes the whole table wholesale
(DELETE + INSERT) every interval, so there is no UPSERT or unique constraint.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel  # noqa: F401 - migrations may reference SQLModel types

from alembic import op

revision: str = "a3f9c1d7b2e8"
down_revision: str | None = "c4f1a9e2b8d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_observations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("request_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_cost_usd", sa.Float(), server_default="0", nullable=False),
        sa.Column("error_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("p50_ms", sa.Integer(), nullable=True),
        sa.Column("p95_ms", sa.Integer(), nullable=True),
        sa.Column("last_seen", sa.Float(), nullable=True),
        sa.Column("window_start", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("window_end", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "refreshed_at",
            sqlmodel.sql.sqltypes.AutoString(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("agent_observations", schema=None) as batch_op:
        batch_op.create_index("idx_agent_obs_agent_id", ["agent_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("agent_observations", schema=None) as batch_op:
        batch_op.drop_index("idx_agent_obs_agent_id")
    op.drop_table("agent_observations")
