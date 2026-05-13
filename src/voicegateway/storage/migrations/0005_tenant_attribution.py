"""Migration 0005: multi-tenant cost attribution.

Introduced in v0.4.0 to back REQ-VG-TENANT-001 (tag sessions with a
tenant dimension) and REQ-VG-TENANT-003 (issue virtual API keys).

Idempotent. Re-running on an already-migrated database is a no-op:

* ``CREATE TABLE IF NOT EXISTS virtual_keys`` and the index
  ``CREATE INDEX IF NOT EXISTS`` guard themselves.
* The ``ALTER TABLE ... ADD COLUMN tenant_id`` calls are guarded by a
  ``PRAGMA table_info`` lookup per table.
* The per-version-dependent ``ALTER``s (on ``turns``,
  ``dead_air_events``, and the four ``replay_*`` tables) are
  additionally guarded by a ``sqlite_master`` presence check, so the
  migration is safe on any v0.0.5+ baseline (v0.0.5-only installs
  skip the v0.2.0 and v0.3.0 table ALTERs silently). This matches the
  Foundry's OQ4 resolution.

Pre-v0.4.0 rows in every table keep ``tenant_id = NULL``. The dashboard
renders NULL as "unattributed" with a muted style (REQ-VG-TENANT-001
AC-3). No backfill to a literal string per OQ3.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite


# Tables that get a `tenant_id TEXT NULL` column. The first two are
# v0.0.5 baseline; the remainder are conditional on prior migrations
# having shipped (turns/dead_air -> migration 0003; replay_* -> 0004).
_BASELINE_TENANT_TABLES = ("sessions", "requests")
_CONDITIONAL_TENANT_TABLES = (
    "turns",
    "dead_air_events",
    "replay_stt_events",
    "replay_llm_tokens",
    "replay_tts_frames",
    "replay_state_snapshots",
)


_VIRTUAL_KEYS_DDL = """
CREATE TABLE IF NOT EXISTS virtual_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    tenant_id TEXT,
    issued_by TEXT,
    issued_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_used_at TEXT,
    revoked_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_virtual_keys_prefix
    ON virtual_keys(key_prefix);

CREATE INDEX IF NOT EXISTS idx_virtual_keys_tenant_id
    ON virtual_keys(tenant_id);
"""


async def _table_exists(db: aiosqlite.Connection, name: str) -> bool:
    """Return True when ``name`` is a real table in this database."""
    cursor = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    )
    row = await cursor.fetchone()
    return row is not None


async def _add_tenant_id_column(db: aiosqlite.Connection, table: str) -> None:
    """Add ``tenant_id TEXT`` to ``table`` if the column does not exist.

    Caller has already verified the table exists; this helper only
    runs the PRAGMA + ALTER pair.
    """
    cursor = await db.execute(f"PRAGMA table_info({table})")
    existing = {row[1] async for row in cursor}
    if "tenant_id" in existing:
        return
    try:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN tenant_id TEXT")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc).lower():
            raise


async def apply(db: aiosqlite.Connection) -> None:
    """Apply migration 0005 against the given connection.

    Steps:

    1. Create the ``virtual_keys`` table + two indexes
       (``key_prefix``, ``tenant_id``) via ``CREATE ... IF NOT EXISTS``.
    2. Add ``tenant_id`` to the two v0.0.5 baseline tables
       (``sessions``, ``requests``) unconditionally (they exist on
       every v0.0.5+ install).
    3. For each conditional table (``turns``, ``dead_air_events``,
       the four ``replay_*`` tables), check ``sqlite_master`` first
       and skip the ALTER if the table is absent. This is the OQ4
       guard: v0.0.5-only installs that have not migrated through
       0003 or 0004 don't error on the missing tables.

    The caller is responsible for committing the transaction; the
    sqlite.py ensure path commits after the migration chain runs.
    """
    await db.executescript(_VIRTUAL_KEYS_DDL)

    for table in _BASELINE_TENANT_TABLES:
        await _add_tenant_id_column(db, table)

    for table in _CONDITIONAL_TENANT_TABLES:
        if await _table_exists(db, table):
            await _add_tenant_id_column(db, table)


__all__ = ["apply"]
