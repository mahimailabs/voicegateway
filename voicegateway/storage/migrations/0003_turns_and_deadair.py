"""Migration 0003: turns table, dead_air_events table, sessions aggregates.

Introduced in v0.2.0 to back REQ-VG-METRICS-001 (per-minute cost),
REQ-VG-METRICS-002 (response speed), REQ-VG-METRICS-003 (talk-over rate
data), REQ-VG-METRICS-004 (dead-air events), and REQ-VG-METRICS-006
(graceful handling of older sessions).

Idempotent. Re-running on an already-migrated database is a no-op:

* ``CREATE TABLE IF NOT EXISTS`` and ``CREATE INDEX IF NOT EXISTS`` are
  the inner SQL's own no-op guards.
* The ``ALTER TABLE sessions ADD COLUMN ...`` additions are guarded by
  a ``PRAGMA table_info(sessions)`` lookup; only columns missing from
  the live schema are added.

v0.0.5- and v0.1.x-era rows in the ``sessions`` table get NULL on the
five new aggregate columns. No backfill (Foundry: "v0.0.5-era rows
keep NULL on new columns. No backfill.").

The Foundry test strategy ``tests/storage/test_turns_migration.py``
(T20) replays this migration on a frozen v0.0.5 fixture and asserts:
tables created, columns added, indexes present, v0.0.5 rows preserved
with NULL on new columns, idempotent re-run.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite


_NEW_TABLES = """
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
);

CREATE INDEX IF NOT EXISTS idx_turns_session_id ON turns(session_id);
CREATE INDEX IF NOT EXISTS idx_turns_response_speed ON turns(response_speed_ms);

CREATE TABLE IF NOT EXISTS dead_air_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    started_at_ms INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    threshold_used_ms INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_dead_air_session_id ON dead_air_events(session_id);
"""


# (column_name, column_type) pairs added to the existing sessions table.
# Each is nullable so v0.0.5- and v0.1.x-era rows are preserved untouched.
_SESSIONS_AGGREGATE_COLUMNS = (
    ("talk_time_seconds", "REAL"),
    ("per_minute_cost_usd", "REAL"),
    ("response_speed_p50_ms", "INTEGER"),
    ("response_speed_p95_ms", "INTEGER"),
    ("talk_over_rate", "REAL"),
)


async def apply(db: aiosqlite.Connection) -> None:
    """Apply migration 0003 against the given connection.

    Steps:

    1. Create ``turns`` and ``dead_air_events`` tables with their
       indexes via a single ``executescript`` call. All inner SQL uses
       ``CREATE ... IF NOT EXISTS`` so the call is idempotent.
    2. Inspect the ``sessions`` table via ``PRAGMA table_info``. For
       each of the five aggregate columns, add it if missing. ALTER
       TABLE cannot be guarded with ``IF NOT EXISTS`` in SQLite, so
       the PRAGMA lookup is the only idempotency mechanism here.

    The caller is responsible for committing the transaction (or
    running this inside one). Most callers will let the underlying
    ``sqlite.py`` connection's auto-commit handle persistence.
    """
    await db.executescript(_NEW_TABLES)

    cursor = await db.execute("PRAGMA table_info(sessions)")
    existing = {row[1] async for row in cursor}
    for column, type_ in _SESSIONS_AGGREGATE_COLUMNS:
        if column not in existing:
            try:
                await db.execute(f"ALTER TABLE sessions ADD COLUMN {column} {type_}")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise


__all__ = ["apply"]
