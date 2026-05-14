"""Baseline: post-0007 schema for every legacy + new table + the two views.

The body is byte-equivalent to the legacy ``storage/migrator.py`` +
``storage/schema.py`` + runtime backfills: every CREATE uses
``IF NOT EXISTS`` so the upgrade is idempotent against:

  - a fresh DB (everything gets built)
  - a partial-legacy DB (e.g. test fixtures that seed only ``requests``;
    the missing tables get filled in here)
  - a fully-migrated legacy DB stamped at baseline (the upgrade is a
    no-op against the already-present tables; 0002-0006 then run
    forward, each PRAGMA-guarded so re-applying them is also a no-op)

Stamping logic in ``Database._stamp_legacy_db_if_needed`` skips this
upgrade only when the legacy DB matches the FULL baseline shape; any
partial-legacy DB falls through to running this upgrade so missing
tables get filled in.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-14
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_BASE_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS requests (
        id TEXT PRIMARY KEY,
        timestamp REAL NOT NULL,
        project TEXT NOT NULL DEFAULT 'default',
        modality TEXT NOT NULL,
        model_id TEXT NOT NULL,
        provider TEXT NOT NULL,
        input_units REAL DEFAULT 0,
        output_units REAL DEFAULT 0,
        cost_usd REAL DEFAULT 0,
        pricing_source TEXT NOT NULL DEFAULT '',
        ttfb_ms REAL,
        total_latency_ms REAL,
        status TEXT DEFAULT 'success',
        fallback_from TEXT,
        error_message TEXT,
        metadata TEXT,
        session_id TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_requests_timestamp ON requests(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_requests_model ON requests(model_id)",
    "CREATE INDEX IF NOT EXISTS idx_requests_modality ON requests(modality)",
    """
    CREATE TABLE IF NOT EXISTS managed_providers (
        provider_id TEXT PRIMARY KEY,
        provider_type TEXT NOT NULL,
        api_key_encrypted TEXT NOT NULL DEFAULT '',
        base_url TEXT,
        extra_config TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        project TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_managed_providers_type ON managed_providers(provider_type)",
    """
    CREATE TABLE IF NOT EXISTS managed_models (
        model_id TEXT PRIMARY KEY,
        modality TEXT NOT NULL,
        provider_id TEXT NOT NULL,
        model_name TEXT NOT NULL,
        display_name TEXT,
        default_language TEXT,
        default_voice TEXT,
        extra_config TEXT NOT NULL DEFAULT '{}',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_managed_models_modality ON managed_models(modality)",
    "CREATE INDEX IF NOT EXISTS idx_managed_models_provider ON managed_models(provider_id)",
    """
    CREATE TABLE IF NOT EXISTS managed_projects (
        project_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        daily_budget REAL NOT NULL DEFAULT 0,
        budget_action TEXT NOT NULL DEFAULT 'warn',
        default_stack TEXT,
        stt_model TEXT,
        llm_model TEXT,
        tts_model TEXT,
        tags TEXT NOT NULL DEFAULT '[]',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        project TEXT NOT NULL,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        modalities TEXT NOT NULL DEFAULT '',
        total_cost_usd REAL DEFAULT 0,
        request_count INTEGER DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON sessions(started_at)",
    """
    CREATE TABLE IF NOT EXISTS config_audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        action TEXT NOT NULL,
        changes_json TEXT,
        source TEXT NOT NULL DEFAULT 'api'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON config_audit_log(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_audit_entity ON config_audit_log(entity_type, entity_id)",
]


_VIEWS = [
    "DROP VIEW IF EXISTS daily_costs",
    """
    CREATE VIEW IF NOT EXISTS daily_costs AS
    SELECT
        date(timestamp, 'unixepoch') as day,
        modality,
        model_id,
        provider,
        COUNT(*) as request_count,
        SUM(cost_usd) as total_cost,
        AVG(ttfb_ms) as avg_ttfb,
        AVG(total_latency_ms) as avg_latency
    FROM requests
    GROUP BY day, modality, model_id, provider
    """,
    "DROP VIEW IF EXISTS project_daily_costs",
    """
    CREATE VIEW IF NOT EXISTS project_daily_costs AS
    SELECT
        project,
        date(timestamp, 'unixepoch') as day,
        modality,
        model_id,
        COUNT(*) as request_count,
        SUM(cost_usd) as total_cost,
        AVG(ttfb_ms) as avg_ttfb
    FROM requests
    GROUP BY project, day, modality, model_id
    """,
]


_BACKFILL_COLUMNS = (
    ("requests", "project", "TEXT NOT NULL DEFAULT 'default'"),
    ("requests", "pricing_source", "TEXT NOT NULL DEFAULT ''"),
    ("requests", "session_id", "TEXT"),
    ("managed_providers", "project", "TEXT"),
)


# Indexes that depend on backfilled columns. They run after the
# backfill step so legacy DBs that lack ``session_id`` or ``project``
# don't fail the CREATE INDEX on the missing column.
_POST_BACKFILL_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_requests_project ON requests(project)",
    "CREATE INDEX IF NOT EXISTS idx_requests_project_timestamp ON requests(project, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_requests_session_id ON requests(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_managed_providers_project ON managed_providers(project)",
)


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    rows = bind.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def _add_column_if_missing(table: str, column: str, sql_type: str) -> None:
    if column not in _existing_columns(table):
        op.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")


def upgrade() -> None:
    for stmt in _BASE_TABLES:
        op.execute(stmt)
    for table, column, sql_type in _BACKFILL_COLUMNS:
        _add_column_if_missing(table, column, sql_type)
    for stmt in _POST_BACKFILL_INDEXES:
        op.execute(stmt)
    for stmt in _VIEWS:
        op.execute(stmt)


def downgrade() -> None:
    """No-op: SQLite migrations are forward-only."""
