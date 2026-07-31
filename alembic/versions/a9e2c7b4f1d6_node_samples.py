"""create node_samples (layer 7: Prometheus scrapes of the boxes)

Revision ID: a9e2c7b4f1d6
Revises: d4b1f6c8a927
Create Date: 2026-07-31 10:00:00.000000

One row per scrape of one node's Prometheus exposition, written by
``middleware/node_samples_worker_middleware.py``. This is the infra layer that
makes "the knee at 25 concurrent calls happened while ``filefd_allocated``
reached ``filefd_maximum`` on sfu-2" a statement about data instead of a guess.

**No foreign key to ``calls``, and no ``call_id`` column.** livekit-server's
packet/nack counters and livekit-sip's invite/call counters are NODE counters;
attributing one to a call would fabricate a per-call measurement that is not
observable server-side. Layer 7 correlates by ``(node, time window)``.

Every value column is nullable and means "not measured" when NULL. Nothing here
is created NOT NULL with a 0 default: a 0 in ``packets_total`` reads as a node
with no traffic, which is a different claim from "the series was not there".

``packet_bytes_total``, ``filefd_maximum`` and the memory columns are
``BigInteger``. A busy SFU passes 2 GiB of RTP within hours and ``fs.file-max``
is routinely ~9.2e18; an INT4 takes neither, silently on SQLite and as a 500 on
PostgreSQL (the trap that already hit the worker byte-count columns).

Two indexes, no more, because this table takes ~57k inserts/day: one on
``(node, source, at_ms)`` for the read path, one on ``at_ms`` for the two trims
(the scrape worker's unconditional one and the per-project retention pass).
There is deliberately no index on ``project`` -- retention filters by age first
and this table has one project in practice.

Chain: revises ``d4b1f6c8a927`` (call_leg timestamp provenance), which revises
``f7c3b9d2e845`` (diagnostics_runs) -> ``e4a7c2f9b1d3`` (calls + call_legs) ->
``06836270c254``. One linear chain, one head. A second revision sharing this
parent forks the graph, which passes every check on SQLite and fails only at
``alembic upgrade head`` on PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9e2c7b4f1d6"
down_revision: str | None = "d4b1f6c8a927"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "node_samples",
        sa.Column("id", sa.Integer(), nullable=False),
        # The operator's target name, not the exporter's node_id label:
        # livekit-server re-mints node_id per process unless pinned, which would
        # split one host's series at every restart.
        sa.Column("node", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        # Epoch milliseconds (~1.8e12), same BigInteger reasoning as `calls`.
        sa.Column("at_ms", sa.BigInteger(), nullable=False),
        sa.Column("project", sa.String(), nullable=False),
        # 'ok' | 'timeout' | 'unreachable' | 'http_error' | 'too_large' |
        # 'unparseable'. A failed scrape still writes a row.
        sa.Column("outcome", sa.String(), nullable=False),
        # NULL when the scrape produced no body; 0 with outcome='ok' means the
        # exposition was fine and every expected series name was missing.
        sa.Column("series_found", sa.Integer(), nullable=True),
        # livekit-server
        sa.Column("rooms", sa.Integer(), nullable=True),
        sa.Column("participants", sa.Integer(), nullable=True),
        sa.Column("packets_total", sa.BigInteger(), nullable=True),
        sa.Column("packet_bytes_total", sa.BigInteger(), nullable=True),
        sa.Column("nacks_total", sa.BigInteger(), nullable=True),
        # livekit-sip (fleet aggregates; blind to anything per-call)
        sa.Column("sip_calls_active", sa.Integer(), nullable=True),
        sa.Column("sip_invite_requests_raw_total", sa.BigInteger(), nullable=True),
        sa.Column("sip_invite_requests_total", sa.BigInteger(), nullable=True),
        sa.Column("sip_invite_accepted_total", sa.BigInteger(), nullable=True),
        sa.Column("sip_calls_terminated_total", sa.BigInteger(), nullable=True),
        # node_exporter (host)
        sa.Column("filefd_allocated", sa.BigInteger(), nullable=True),
        sa.Column("filefd_maximum", sa.BigInteger(), nullable=True),
        sa.Column("load1", sa.Float(), nullable=True),
        sa.Column("cpu_seconds_total", sa.Float(), nullable=True),
        sa.Column("cpu_idle_seconds_total", sa.Float(), nullable=True),
        sa.Column("memory_available_bytes", sa.BigInteger(), nullable=True),
        sa.Column("memory_total_bytes", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_node_samples_node_source_at_ms",
        "node_samples",
        ["node", "source", "at_ms"],
        unique=False,
    )
    op.create_index("idx_node_samples_at_ms", "node_samples", ["at_ms"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_node_samples_at_ms", table_name="node_samples")
    op.drop_index("idx_node_samples_node_source_at_ms", table_name="node_samples")
    op.drop_table("node_samples")
