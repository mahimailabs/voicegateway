"""add agent_id to requests (fleet per-agent attribution)

Revision ID: b2e7c4a9f1d3
Revises: a7c2e91f8d34
Create Date: 2026-06-04 06:00:00.000000

Phase 1 of the fleet collector integration. When N agents push telemetry
to one collector, each request row needs to be attributable to the agent
that produced it. ``agent_id`` is a self-reported label (env
``VOICEGW_AGENT_ID`` or hostname), nullable so existing rows and
non-fleet single-node writes stay valid. Indexed singly and as a
``(agent_id, timestamp)`` composite, matching the existing project index
convention, so per-agent fleet queries (Phase 2) are cheap.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2e7c4a9f1d3"
down_revision: str | None = "a7c2e91f8d34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Direct add_column (not batch_alter_table): SQLite supports ALTER TABLE
    # ADD COLUMN directly, and batch mode triggers a full table rename that
    # breaks any view referencing ``requests`` (e.g. ``daily_costs``). Same
    # rationale as the cached_input_units migration.
    op.add_column(
        "requests",
        sa.Column("agent_id", sa.String(), nullable=True),
    )
    op.create_index("idx_requests_agent_id", "requests", ["agent_id"])
    op.create_index(
        "idx_requests_agent_id_timestamp", "requests", ["agent_id", "timestamp"]
    )


def downgrade() -> None:
    op.drop_index("idx_requests_agent_id_timestamp", table_name="requests")
    op.drop_index("idx_requests_agent_id", table_name="requests")
    op.drop_column("requests", "agent_id")
