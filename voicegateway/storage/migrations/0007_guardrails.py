"""Migration 0007: v0.6.0 LLM-side guardrails.

Adds the guardrail audit table plus per-session/project policy columns.
Idempotent, matching the prior migration style.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite


_GUARDRAIL_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS guardrail_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL CHECK (event_type IN ('fired', 'bypassed')),
    session_id TEXT NOT NULL,
    tenant_id TEXT,
    turn_index INTEGER,
    category TEXT,
    action TEXT,
    context_excerpt TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_guardrail_events_session_id
    ON guardrail_events(session_id);
CREATE INDEX IF NOT EXISTS idx_guardrail_events_created_at
    ON guardrail_events(created_at);
CREATE INDEX IF NOT EXISTS idx_guardrail_events_category
    ON guardrail_events(category);
CREATE INDEX IF NOT EXISTS idx_guardrail_events_action
    ON guardrail_events(action);
CREATE INDEX IF NOT EXISTS idx_guardrail_events_tenant_id
    ON guardrail_events(tenant_id);
"""

_SESSIONS_NEW_COLUMNS = (
    ("guardrails_active", "INTEGER"),
    ("guardrails_bypassed", "INTEGER"),
    ("guardrail_policy_snapshot_json", "TEXT"),
)

_MANAGED_PROJECTS_NEW_COLUMNS = (("guardrail_policy_json", "TEXT"),)


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
    try:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc).lower():
            raise


async def apply(db: aiosqlite.Connection) -> None:
    await db.executescript(_GUARDRAIL_EVENTS_DDL)

    for column, sql_type in _SESSIONS_NEW_COLUMNS:
        await _add_column_if_missing(db, "sessions", column, sql_type)

    if await _table_exists(db, "managed_projects"):
        for column, sql_type in _MANAGED_PROJECTS_NEW_COLUMNS:
            await _add_column_if_missing(db, "managed_projects", column, sql_type)


__all__ = ["apply"]
