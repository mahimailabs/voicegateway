"""widen the turns millisecond columns to BIGINT

Revision ID: c5b1e83d0f47
Revises: e4a7c2b9d013
Create Date: 2026-08-09 06:00:00.000000

Every turn insert failed on Postgres:

    DataError: invalid input for query argument $3 in element #0 of
    executemany() sequence: 6886684364 (value out of int32 range)

which surfaced as HTTP 503 from ``POST /v1/ingest/turns``, so the table stayed
empty and ``e2e_ms`` on ``/v1/rooms/{room}/latency`` stayed null.

``Turn`` declared the five ``_ms`` columns as plain ``int``, which SQLModel maps
to INTEGER, int32, capped at 2147483647. The values stored there are
millisecond timestamps and are far larger.

SQLite is why this survived: its dynamic typing stores an oversized value in an
INTEGER column without complaint, so the whole SQLite test suite passed while
Postgres rejected every row. ``clickhouse/migrations/0003_turns.sql`` had
declared all five Int64 from the start, so the two stores already disagreed
about the same field.

``response_speed_ms`` is a delta and fits in int32. It is widened anyway so the
row is uniform and no future reader has to remember which of five sibling
columns is the narrow one.

SQLite is skipped, not because it is safe to skip, but because it is already
correct: the column type is advisory there, the values round-trip today, and
``ALTER COLUMN TYPE`` does not exist in its dialect. Attempting it would break
the migration on the default backend to fix a problem that backend does not
have.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c5b1e83d0f47"
down_revision: str | None = "e4a7c2b9d013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    ("caller_speak_start_ms", False),
    ("caller_speak_end_ms", False),
    ("agent_speak_start_ms", True),
    ("agent_speak_end_ms", True),
    ("response_speed_ms", True),
)


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    for name, nullable in _COLUMNS:
        op.alter_column(
            "turns",
            name,
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=nullable,
        )


def downgrade() -> None:
    # Narrowing is lossy by construction: any row written since the upgrade
    # holds a value that does not fit, and Postgres will refuse the ALTER rather
    # than truncate. That refusal is correct, so it is left to surface rather
    # than being papered over with a USING clause that would silently corrupt
    # every timestamp.
    if op.get_bind().dialect.name == "sqlite":
        return
    for name, nullable in _COLUMNS:
        op.alter_column(
            "turns",
            name,
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=nullable,
        )
