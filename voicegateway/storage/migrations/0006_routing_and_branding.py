"""Migration 0006: cross-modality routing + per-project white-label branding.

Introduced in v0.5.0 to back REQ-VG-ROUTE-001..004.

Idempotent. Re-running on an already-migrated database is a no-op:

* ``CREATE TABLE IF NOT EXISTS latency_observations`` and the index
  ``CREATE INDEX IF NOT EXISTS`` guard themselves.
* The ``ALTER TABLE ... ADD COLUMN`` calls are guarded by a
  ``PRAGMA table_info`` lookup per (table, column).

Two tables are touched:

* ``sessions`` (v0.0.5 baseline) gets five new nullable columns:
  ``budget_ms``, ``budget_ms_used``, ``budget_overrun``,
  ``routed_llm``, ``routed_tts``. Speech-to-text on the sessions row
  is the existing ``stt_provider`` field carried from v0.0.5 — no
  new column there.
* ``projects`` (v0.0.5 baseline; only present on installs that have
  configured projects in voicegw.yaml or via the dashboard) gets
  ``branding_json TEXT NULL``. Guarded by a ``sqlite_master``
  presence check so a v0.0.5-baseline install without a projects
  table (the auto-default-project case) doesn't error.

The new ``latency_observations`` table is denormalized: one row per
(project, provider, modality) holding the latest p50/p95 from the
roll-up worker (T06). Composite index on
(project_id, provider, modality) keeps router reads fast.

Pre-v0.5.0 rows in every table keep the new fields NULL. The
dashboard renders NULL budget_overrun as "ok" and NULL routed_* as
"-". No backfill.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite


# (table, column) tuples to add as nullable columns. None of these
# require defaults; pre-v0.5.0 rows stay NULL and the read side
# treats NULL as "this session predates routing".
_SESSIONS_NEW_COLUMNS = (
    ("budget_ms", "INTEGER"),
    ("budget_ms_used", "INTEGER"),
    ("budget_overrun", "INTEGER"),
    ("routed_llm", "TEXT"),
    ("routed_tts", "TEXT"),
)


_PROJECTS_NEW_COLUMNS = (("branding_json", "TEXT"),)


_LATENCY_OBSERVATIONS_DDL = """
CREATE TABLE IF NOT EXISTS latency_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    modality TEXT NOT NULL,
    p50_ms INTEGER,
    p95_ms INTEGER,
    sample_count INTEGER NOT NULL DEFAULT 0,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    refreshed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_latency_obs_project_provider
    ON latency_observations(project_id, provider, modality);
"""


async def _table_exists(db: aiosqlite.Connection, name: str) -> bool:
    cursor = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    )
    row = await cursor.fetchone()
    return row is not None


async def _add_column_if_missing(
    db: aiosqlite.Connection, table: str, column: str, sql_type: str
) -> None:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    existing = {row[1] async for row in cursor}
    if column in existing:
        return
    await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")


async def apply(db: aiosqlite.Connection) -> None:
    """Apply migration 0006 against the given connection.

    Steps:

    1. Create the ``latency_observations`` table + composite index.
    2. Add the five routing columns to ``sessions``. The table is
       always present on a v0.0.5+ baseline.
    3. Add ``branding_json`` to ``projects`` IFF the table exists.
       Some early installs that never wrote a project entry may
       not have a ``managed_projects`` table — guard the ALTER
       accordingly. Same OQ4-style pattern v0.4.0's 0005 used for
       the v0.2.0 / v0.3.0 derived tables.

    The caller commits the transaction; the sqlite.py ensure path
    commits after the migration chain runs.
    """
    await db.executescript(_LATENCY_OBSERVATIONS_DDL)

    for column, sql_type in _SESSIONS_NEW_COLUMNS:
        await _add_column_if_missing(db, "sessions", column, sql_type)

    # ``managed_projects`` is the v0.0.5 projects table per
    # sqlite.py. Foundry's prose said "projects.branding_json"; the
    # actual table name on disk is ``managed_projects``. Guard for
    # presence: a fresh ensure path creates it, but defensive
    # programming costs one PRAGMA.
    if await _table_exists(db, "managed_projects"):
        for column, sql_type in _PROJECTS_NEW_COLUMNS:
            await _add_column_if_missing(db, "managed_projects", column, sql_type)


__all__ = ["apply"]
