"""Replay event tables + sessions replay-size aggregate.

Ported from ``voicegateway/storage/migrations/0004_replay_tables.py``.

Revision ID: 0003_replay_tables
Revises: 0002_turns_and_deadair
Create Date: 2026-05-14
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0003_replay_tables"
down_revision: str | None = "0002_turns_and_deadair"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_REPLAY_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS replay_stt_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        t_ms INTEGER NOT NULL,
        payload TEXT NOT NULL,
        provider TEXT NOT NULL DEFAULT '',
        cost_usd REAL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_replay_stt_session_t ON replay_stt_events(session_id, t_ms)",
    """
    CREATE TABLE IF NOT EXISTS replay_llm_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        t_ms INTEGER NOT NULL,
        payload TEXT NOT NULL,
        provider TEXT NOT NULL DEFAULT '',
        cost_usd REAL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_replay_llm_session_t ON replay_llm_tokens(session_id, t_ms)",
    """
    CREATE TABLE IF NOT EXISTS replay_tts_frames (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        t_ms INTEGER NOT NULL,
        payload TEXT NOT NULL,
        provider TEXT NOT NULL DEFAULT '',
        cost_usd REAL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_replay_tts_session_t ON replay_tts_frames(session_id, t_ms)",
    """
    CREATE TABLE IF NOT EXISTS replay_state_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        t_ms INTEGER NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_replay_state_session_t ON replay_state_snapshots(session_id, t_ms)",
]


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    rows = bind.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def upgrade() -> None:
    for stmt in _REPLAY_TABLES:
        op.execute(stmt)

    if "replay_size_bytes" not in _existing_columns("sessions"):
        op.execute("ALTER TABLE sessions ADD COLUMN replay_size_bytes INTEGER")


def downgrade() -> None:
    """No-op: SQLite migrations are forward-only."""
