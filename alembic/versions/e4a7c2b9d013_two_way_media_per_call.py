"""count answered calls that received no audio back

Revision ID: e4a7c2b9d013
Revises: a1c6e39b7f24
Create Date: 2026-08-05 04:40:00.000000

``load_run_tests`` already carries per-test RTP totals, and those totals cannot
answer whether each call carried audio. A test where every call is healthy and a
test where half the calls are silent and half carry double report the same
packets received.

That is not a hypothetical distinction. A 24 hour run reported 100%
establishment, zero failed calls and a 0.496 received-per-sent ratio while
12,198 of its 28,804 calls had received nothing at all. The ratio was in the
report and no gate read it, so the run was presented as clean.

Both columns are nullable and NULL is not zero: it means the per-call records
were absent, unreadable, or of a schema the parser does not map. The gate that
reads them returns UNKNOWN in that case rather than PASS, because a run whose
media nobody counted has not demonstrated two-way media.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e4a7c2b9d013"
down_revision: str | None = "a1c6e39b7f24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "load_run_tests",
        sa.Column("calls_answered_with_inbound", sa.Integer(), nullable=True),
    )
    op.add_column(
        "load_run_tests",
        sa.Column("calls_answered_without_inbound", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("load_run_tests", "calls_answered_without_inbound")
    op.drop_column("load_run_tests", "calls_answered_with_inbound")
