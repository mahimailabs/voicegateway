"""LLM-side guardrails: events table + sessions snapshot + project policy.

Ported from ``voicegateway/storage/migrations/0007_guardrails.py``.

Revision ID: 0006_guardrails
Revises: 0005_routing_and_branding
Create Date: 2026-05-14
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0006_guardrails"
down_revision: str | None = "0005_routing_and_branding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_GUARDRAIL_EVENTS_TABLE = """
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
)
"""

_GUARDRAIL_EVENT_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_guardrail_events_session_id ON guardrail_events(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_guardrail_events_created_at ON guardrail_events(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_guardrail_events_category ON guardrail_events(category)",
    "CREATE INDEX IF NOT EXISTS idx_guardrail_events_action ON guardrail_events(action)",
    "CREATE INDEX IF NOT EXISTS idx_guardrail_events_tenant_id ON guardrail_events(tenant_id)",
)


_SESSIONS_NEW_COLUMNS = (
    ("guardrails_active", "INTEGER"),
    ("guardrails_bypassed", "INTEGER"),
    ("guardrail_policy_snapshot_json", "TEXT"),
)


_MANAGED_PROJECTS_NEW_COLUMNS = (("guardrail_policy_json", "TEXT"),)


def _table_exists(table: str) -> bool:
    bind = op.get_bind()
    row = bind.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": table},
    ).fetchone()
    return row is not None


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    rows = bind.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def _add_column_if_missing(table: str, column: str, sql_type: str) -> None:
    if column not in _existing_columns(table):
        op.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")


def upgrade() -> None:
    op.execute(_GUARDRAIL_EVENTS_TABLE)
    for stmt in _GUARDRAIL_EVENT_INDEXES:
        op.execute(stmt)

    for column, sql_type in _SESSIONS_NEW_COLUMNS:
        _add_column_if_missing("sessions", column, sql_type)

    if _table_exists("managed_projects"):
        for column, sql_type in _MANAGED_PROJECTS_NEW_COLUMNS:
            _add_column_if_missing("managed_projects", column, sql_type)


def downgrade() -> None:
    """No-op: SQLite migrations are forward-only."""
