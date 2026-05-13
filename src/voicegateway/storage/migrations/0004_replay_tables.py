"""Migration 0004: replay event tables + session replay-size aggregate.

Introduced in v0.3.0 to back REQ-VG-REPLAY-001 (open any past
conversation), REQ-VG-REPLAY-003 (every captured event), REQ-VG-REPLAY-005
(state at every moment), and REQ-VG-REPLAY-006 (graceful pre-v0.3.0
handling).

Idempotent. Re-running on an already-migrated database is a no-op:

* ``CREATE TABLE IF NOT EXISTS`` and ``CREATE INDEX IF NOT EXISTS`` are
  the inner SQL's own no-op guards.
* The ``ALTER TABLE sessions ADD COLUMN replay_size_bytes`` is guarded
  by a ``PRAGMA table_info(sessions)`` lookup.

Pre-v0.3.0 rows in the ``sessions`` table get NULL on
``replay_size_bytes``. v0.2.0 aggregate columns (added by migration
0003) are untouched.

Four new tables share an identical column shape so a single SQL
DDL block does all four. The dashboard ``Replay.tsx`` page (T11)
fetches a time-ordered union of all four via ``replay.read_full_replay``
(T05).
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite


_NEW_TABLES = """
CREATE TABLE IF NOT EXISTS replay_stt_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    t_ms INTEGER NOT NULL,
    payload TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    cost_usd REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_replay_stt_session_t
    ON replay_stt_events(session_id, t_ms);

CREATE TABLE IF NOT EXISTS replay_llm_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    t_ms INTEGER NOT NULL,
    payload TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    cost_usd REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_replay_llm_session_t
    ON replay_llm_tokens(session_id, t_ms);

CREATE TABLE IF NOT EXISTS replay_tts_frames (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    t_ms INTEGER NOT NULL,
    payload TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    cost_usd REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_replay_tts_session_t
    ON replay_tts_frames(session_id, t_ms);

CREATE TABLE IF NOT EXISTS replay_state_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    t_ms INTEGER NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_replay_state_session_t
    ON replay_state_snapshots(session_id, t_ms);
"""


# Single nullable column added to the existing sessions table. v0.2.0's
# aggregate columns (talk_time_seconds, per_minute_cost_usd, etc.) are
# untouched; this migration is additive only.
_SESSIONS_REPLAY_COLUMN = ("replay_size_bytes", "INTEGER")


async def apply(db: aiosqlite.Connection) -> None:
    """Apply migration 0004 against the given connection.

    Steps:

    1. ``executescript`` creates the four replay tables + four indexes
       via ``CREATE ... IF NOT EXISTS`` no-op guards.
    2. ``PRAGMA table_info(sessions)`` is inspected; if
       ``replay_size_bytes`` is missing, ``ALTER TABLE`` adds it.
       PRAGMA guard is the idempotency mechanism (SQLite has no
       ``ADD COLUMN IF NOT EXISTS``).

    Caller manages the connection lifecycle; T08 wires this into
    ``SQLiteStorage._ensure_initialized`` after migration 0003.
    """
    await db.executescript(_NEW_TABLES)

    cursor = await db.execute("PRAGMA table_info(sessions)")
    existing = {row[1] async for row in cursor}
    column, type_ = _SESSIONS_REPLAY_COLUMN
    if column not in existing:
        try:
            await db.execute(f"ALTER TABLE sessions ADD COLUMN {column} {type_}")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise


__all__ = ["apply"]
