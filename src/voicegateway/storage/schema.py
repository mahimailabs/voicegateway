"""SQL schema constants for the SQLite storage backend."""

from __future__ import annotations

SCHEMA_SQL = """
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
);

CREATE INDEX IF NOT EXISTS idx_requests_timestamp ON requests(timestamp);
CREATE INDEX IF NOT EXISTS idx_requests_model ON requests(model_id);
CREATE INDEX IF NOT EXISTS idx_requests_modality ON requests(modality);
CREATE INDEX IF NOT EXISTS idx_requests_project ON requests(project);
CREATE INDEX IF NOT EXISTS idx_requests_project_timestamp ON requests(project, timestamp);
-- session_id index created post-migration; see migrator.initialize().
-- A pre-v0.0.5 file will not yet have the column when this script runs,
-- so creating the index here would fail on the legacy schema.

DROP VIEW IF EXISTS daily_costs;
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
GROUP BY day, modality, model_id, provider;

DROP VIEW IF EXISTS project_daily_costs;
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
GROUP BY project, day, modality, model_id;

CREATE TABLE IF NOT EXISTS managed_providers (
    provider_id TEXT PRIMARY KEY,
    provider_type TEXT NOT NULL,
    api_key_encrypted TEXT NOT NULL DEFAULT '',
    base_url TEXT,
    extra_config TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    project TEXT
);

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
);

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
);

-- Sessions table per design.md section 3.2.
-- One row per logical voice session. Populated by CostTracker on the
-- first request of a session; total_cost_usd and request_count
-- accumulate per request. ended_at stays NULL until the session
-- closes.
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    modalities TEXT NOT NULL DEFAULT '',
    total_cost_usd REAL DEFAULT 0,
    request_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project);
CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON sessions(started_at);
"""


AUDIT_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS config_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    changes_json TEXT,
    source TEXT NOT NULL DEFAULT 'api'
)
"""


__all__ = ["AUDIT_LOG_SCHEMA", "SCHEMA_SQL"]
