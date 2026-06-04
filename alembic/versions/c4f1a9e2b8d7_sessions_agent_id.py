"""add agent_id to sessions (fleet per-agent session filter)

Revision ID: c4f1a9e2b8d7
Revises: b2e7c4a9f1d3
Create Date: 2026-06-04 08:00:00.000000

Phase 2 of the fleet collector. agent_id already lives on the requests table
(Phase 1, b2e7c4a9f1d3); the sessions table needs it too so the dashboard's
Sessions list can filter by agent the same way it filters by tenant (tenant_id
is already on sessions). Nullable: pre-Phase-2 session rows keep NULL and simply
do not match an agent filter. Written + COALESCE-preserved by the sessions
UPSERT in request_log_repository.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4f1a9e2b8d7"
down_revision: str | None = "b2e7c4a9f1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Direct add_column / create_index (not batch_alter_table): same
    # view-collision rationale as the requests agent_id migration.
    op.add_column("sessions", sa.Column("agent_id", sa.String(), nullable=True))
    op.create_index("idx_sessions_agent_id", "sessions", ["agent_id"])


def downgrade() -> None:
    op.drop_index("idx_sessions_agent_id", table_name="sessions")
    op.drop_column("sessions", "agent_id")
