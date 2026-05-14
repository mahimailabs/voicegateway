"""Cross-modality routing + per-project branding + latency observations.

Ported from ``voicegateway/storage/migrations/0006_routing_and_branding.py``.
The managed_projects branding column is added only when the table
exists (early installs that never wrote a project may not have it).

Revision ID: 0005_routing_and_branding
Revises: 0004_tenant_attribution
Create Date: 2026-05-14
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0005_routing_and_branding"
down_revision: str | None = "0004_tenant_attribution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SESSIONS_NEW_COLUMNS = (
    ("budget_ms", "INTEGER"),
    ("budget_ms_used", "INTEGER"),
    ("budget_overrun", "INTEGER"),
    ("routed_llm", "TEXT"),
    ("routed_tts", "TEXT"),
)


_PROJECTS_NEW_COLUMNS = (("branding_json", "TEXT"),)


_LATENCY_TABLE = """
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
)
"""
_LATENCY_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_latency_obs_project_provider "
    "ON latency_observations(project_id, provider, modality)"
)


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
    op.execute(_LATENCY_TABLE)
    op.execute(_LATENCY_INDEX)

    for column, sql_type in _SESSIONS_NEW_COLUMNS:
        _add_column_if_missing("sessions", column, sql_type)

    if _table_exists("managed_projects"):
        for column, sql_type in _PROJECTS_NEW_COLUMNS:
            _add_column_if_missing("managed_projects", column, sql_type)


def downgrade() -> None:
    """No-op: SQLite migrations are forward-only."""
