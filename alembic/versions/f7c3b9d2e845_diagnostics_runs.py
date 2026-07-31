"""create diagnostics_runs

Revision ID: f7c3b9d2e845
Revises: e4a7c2f9b1d3
Create Date: 2026-07-30 01:00:00.000000

Diagnostics runs move out of the process-local dict. They lived in
``_RUNS``/``_ORDER`` inside the dashboard router: trimmed to the newest 20 and
erased by every restart, even though a run places real, billed calls.

The timestamp columns are ``String``, not numeric: they hold the ISO-8601
strings the ``/api/diagnostics/runs`` payload already returns, verbatim, so the
endpoint contract stays byte-identical and retention can compare them lexically
(the same thing the session retention pass does with ``sessions.ended_at``).
``project`` exists only so a run ages out on the per-project retention pass; a
diagnostics run is host-scoped and the column never reaches the wire.

No loss/jitter column and no percentile column: this table stores probe payloads
unchanged and derives nothing.

Forward-only. The in-memory runs of a running process are not migrated in --
they are the state this table replaces, not history worth inventing.

Chain: revises ``e4a7c2f9b1d3`` (calls + call_legs), which was itself the first
of the end-to-end profiling revisions. One linear chain, one head: a second
revision sharing ``e4a7c2f9b1d3`` as its parent forks the graph, which passes
every check on SQLite and only fails at ``alembic upgrade head`` on PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f7c3b9d2e845"
down_revision: str | None = "e4a7c2f9b1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "diagnostics_runs",
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("project", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("checks_json", sa.Text(), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        # NULL means no results were recorded, which is not the same claim as an
        # empty result object.
        sa.Column("results_json", sa.Text(), nullable=True),
        sa.Column("verdict", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        # ISO-8601 UTC strings, stored as served (see the module docstring).
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("started_at", sa.String(), nullable=True),
        sa.Column("ended_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index(
        "idx_diagnostics_runs_created_at",
        "diagnostics_runs",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "idx_diagnostics_runs_project", "diagnostics_runs", ["project"], unique=False
    )


def downgrade() -> None:
    op.drop_index("idx_diagnostics_runs_project", table_name="diagnostics_runs")
    op.drop_index("idx_diagnostics_runs_created_at", table_name="diagnostics_runs")
    op.drop_table("diagnostics_runs")
