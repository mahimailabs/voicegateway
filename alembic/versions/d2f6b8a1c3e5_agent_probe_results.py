"""add agent_probe_results cache table

Revision ID: d2f6b8a1c3e5
Revises: c9d1e5f7a2b4
Create Date: 2026-07-25 18:00:00.000000

Caches the latest probe result per agent so the Agents page can render a
per-agent latency graph without re-running a billed probe on every view: a
probe writes the row, the page reads it, and a new one runs only on demand.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d2f6b8a1c3e5"
down_revision: str | None = "c9d1e5f7a2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_probe_results",
        sa.Column("agent_id", sa.String(), primary_key=True),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("agent_probe_results")
