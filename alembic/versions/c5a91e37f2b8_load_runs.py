"""create load_runs + load_run_tests (an external generator's run, per test)

Revision ID: c5a91e37f2b8
Revises: b3e7c1a95d24
Create Date: 2026-08-01 07:30:00.000000

``diagnostics_runs`` models one VoiceGateway probe and cannot express what a load
run is owed, which is a row per test step carrying concurrency, duration,
establishment counts, peak resource use and failures broken down by cause.

Every measured column is nullable and NULL means "not measured". Nothing is
created NOT NULL with a 0 default: a 0 in ``peak_concurrency`` describes a test
that carried no calls, which is a different claim from "the artifact did not
say", and only the second one may be rendered as absent.

Nothing derived is stored. The counts are here; the establishment rate is
computed from them when it is judged. A stored rate is a number that can
disagree with the counts beside it after one bad import.

``load_runs.id`` is caller-supplied rather than a surrogate, so the calls filed
against a run by ``POST /v1/calls/observations`` (which stamps ``run_id``) can be
found by the same string.

Millisecond columns are ``BigInteger``: they hold epoch milliseconds, which
overflow a PostgreSQL INT4. ``rtp_packets_*`` are ``BigInteger`` too, because a
500-concurrent hour at 50 packets per second per call passes 90 million.

No foreign key from ``load_run_tests`` to ``load_runs``. The rest of this schema
does not use them either, and retention deletes children explicitly in the same
transaction as the parent, which is the pattern ``calls`` and ``call_legs``
already follow.

Indexes: one per read path and one for the age scan retention does. The unique
constraint on ``(run_id, name)`` makes a re-import update rather than duplicate;
both columns are NOT NULL, so it has none of the nullable-key trouble that makes
the rest of this repo avoid native upserts.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c5a91e37f2b8"
down_revision = "b3e7c1a95d24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "load_runs",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column("project", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("tool", sa.String(), nullable=True),
        sa.Column("tool_version", sa.String(), nullable=True),
        sa.Column("artifact_schema_version", sa.String(), nullable=True),
        sa.Column("started_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("ended_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("artifact_sha256", sa.String(), nullable=True),
        sa.Column("artifact_source", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
    )
    op.create_index("idx_load_runs_created_at_ms", "load_runs", ["created_at_ms"])
    op.create_index("idx_load_runs_project", "load_runs", ["project"])

    op.create_table(
        "load_run_tests",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("started_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("ended_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("target_concurrency", sa.Integer(), nullable=True),
        sa.Column("peak_concurrency", sa.Integer(), nullable=True),
        sa.Column("attempted_calls", sa.Integer(), nullable=True),
        sa.Column("succeeded_calls", sa.Integer(), nullable=True),
        sa.Column("failed_calls", sa.Integer(), nullable=True),
        sa.Column("failed_timeout", sa.Integer(), nullable=True),
        sa.Column("failed_unexpected_sip", sa.Integer(), nullable=True),
        sa.Column("failed_transport_error", sa.Integer(), nullable=True),
        sa.Column("failed_parse_error", sa.Integer(), nullable=True),
        sa.Column("failed_scenario_error", sa.Integer(), nullable=True),
        sa.Column("failed_cancelled", sa.Integer(), nullable=True),
        sa.Column("peak_cpu_utilisation", sa.Float(), nullable=True),
        sa.Column("peak_memory_utilisation", sa.Float(), nullable=True),
        sa.Column("node_samples_in_window", sa.Integer(), nullable=True),
        sa.Column("rtp_packets_sent", sa.BigInteger(), nullable=True),
        sa.Column("rtp_packets_received", sa.BigInteger(), nullable=True),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("run_id", "name", name="uq_load_run_tests_run_name"),
    )
    op.create_index("idx_load_run_tests_run_id", "load_run_tests", ["run_id"])
    op.create_index(
        "idx_load_run_tests_run_id_sequence", "load_run_tests", ["run_id", "sequence"]
    )


def downgrade() -> None:
    op.drop_index("idx_load_run_tests_run_id_sequence", table_name="load_run_tests")
    op.drop_index("idx_load_run_tests_run_id", table_name="load_run_tests")
    op.drop_table("load_run_tests")
    op.drop_index("idx_load_runs_project", table_name="load_runs")
    op.drop_index("idx_load_runs_created_at_ms", table_name="load_runs")
    op.drop_table("load_runs")
