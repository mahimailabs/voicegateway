"""create tool_calls

Revision ID: b8e2f04a7d31
Revises: a3f7d21c9b04
Create Date: 2026-08-19 00:45:00.000000

One row per tool call: name, start, duration, outcome, correlated to the turn
and session it belongs to. On agents that call tools the tool is usually the
largest term in a slow turn, and it was the one term the views could not show.

NO COLUMN FOR ARGUMENTS OR RESULTS, deliberately and permanently. A tool's
payload is the operator's data; a name and a duration are a timing measurement.
That separation is what lets this capture default on.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b8e2f04a7d31"
down_revision: str | None = "a3f7d21c9b04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("call_id", sa.String(), nullable=False),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("started_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column("turn_index", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.String(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column("tenant_id", sa.String(), nullable=True),
        sa.Column("revision", sa.String(), nullable=True),
    )
    op.create_index("idx_tool_calls_session_id", "tool_calls", ["session_id"])
    op.create_index("idx_tool_calls_tool_name", "tool_calls", ["tool_name"])


def downgrade() -> None:
    op.drop_index("idx_tool_calls_tool_name", table_name="tool_calls")
    op.drop_index("idx_tool_calls_session_id", table_name="tool_calls")
    op.drop_table("tool_calls")
