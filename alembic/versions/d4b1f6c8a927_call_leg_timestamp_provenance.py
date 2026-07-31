"""add call_legs.joined_at_source + call_legs.first_audio_track_at_source

Revision ID: d4b1f6c8a927
Revises: f7c3b9d2e845
Create Date: 2026-07-30 09:00:00.000000

Provenance for the two timestamps the answer-latency computation subtracts. The
value columns already exist; what was missing is who observed them.

``calls.answer_latency_ms`` is derived as ``agent first_audio_track_at_ms -
caller joined_at_ms``, and the same subtraction means two different things
depending on the writer: a webhook's ``created_at`` is whole SECONDS, while an
agent or load worker that took part in the call reports its own clock in
milliseconds. On a 4 s answer latency a second of truncation is 25% error, so
``answer_latency_source`` cannot honestly distinguish its two weaker rungs
(``agent_report`` vs ``webhook_proxy``) without knowing which writer's value is
stored. These two columns are that knowledge, and nothing else needs them.

Nullable, with no server default and no backfill: a leg written before this
revision has a timestamp whose writer we genuinely do not know, and NULL says
exactly that. The derivation treats NULL as "not known to be self-reported",
which under-claims precision rather than inventing it. Forward-only, like every
revision in this series.

No column is added for a reported answer-latency duration: a reported
``sipp_rtd`` goes straight into the existing ``calls.answer_latency_ms`` under
the precedence rule, so there is one number in one column. And no loss / jitter
/ MOS column, here or anywhere: not observable server-side.

Chain: revises ``f7c3b9d2e845`` (diagnostics_runs), which revises
``e4a7c2f9b1d3`` (calls + call_legs). One linear chain, one head -- a second
revision sharing this parent forks the graph, which passes every check on SQLite
and only fails at ``alembic upgrade head`` on PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4b1f6c8a927"
down_revision: str | None = "f7c3b9d2e845"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Direct add_column, not batch_alter_table: SQLite adds a nullable column in
    # place, and a batch rebuild would drop and recreate the table (the same
    # view-collision hazard the sessions migrations call out).
    op.add_column("call_legs", sa.Column("joined_at_source", sa.String(), nullable=True))
    op.add_column(
        "call_legs",
        sa.Column("first_audio_track_at_source", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("call_legs", "first_audio_track_at_source")
    op.drop_column("call_legs", "joined_at_source")
