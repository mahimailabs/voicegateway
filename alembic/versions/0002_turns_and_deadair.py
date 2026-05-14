"""Turns + dead-air tables + sessions aggregate columns.

Ported from ``voicegateway/storage/migrations/0003_turns_and_deadair.py``.
Body is unchanged in SQL semantics; only the connection mechanism
moves from ``await db.execute(...)`` to ``op.execute(...)``. PRAGMA
guards remain so re-running on a DB that already has the columns
(common after stamping at this revision) is a no-op.

Revision ID: 0002_turns_and_deadair
Revises: 0001_baseline
Create Date: 2026-05-14
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0002_turns_and_deadair"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_NEW_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS turns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        turn_index INTEGER NOT NULL,
        caller_speak_start_ms INTEGER NOT NULL,
        caller_speak_end_ms INTEGER NOT NULL,
        agent_speak_start_ms INTEGER,
        agent_speak_end_ms INTEGER,
        response_speed_ms INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_turns_session_id ON turns(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_turns_response_speed ON turns(response_speed_ms)",
    """
    CREATE TABLE IF NOT EXISTS dead_air_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        started_at_ms INTEGER NOT NULL,
        duration_ms INTEGER NOT NULL,
        threshold_used_ms INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_dead_air_session_id ON dead_air_events(session_id)",
]


_SESSIONS_AGGREGATE_COLUMNS = (
    ("talk_time_seconds", "REAL"),
    ("per_minute_cost_usd", "REAL"),
    ("response_speed_p50_ms", "INTEGER"),
    ("response_speed_p95_ms", "INTEGER"),
    ("talk_over_rate", "REAL"),
)


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    rows = bind.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def upgrade() -> None:
    for stmt in _NEW_TABLES:
        op.execute(stmt)

    existing = _existing_columns("sessions")
    for column, sql_type in _SESSIONS_AGGREGATE_COLUMNS:
        if column not in existing:
            op.execute(f"ALTER TABLE sessions ADD COLUMN {column} {sql_type}")


def downgrade() -> None:
    """No-op: SQLite migrations are forward-only."""
