"""SQLite storage backend for request logs, projects, and cost tracking."""

from __future__ import annotations

import datetime as _dt
import json
import logging
import time
from pathlib import Path
from typing import Any

import aiosqlite

from voicegateway.storage._percentiles import compute_percentiles
from voicegateway.storage.models import RequestRecord

_DEFAULT_PERCENTILES: list[float] = [50.0, 95.0, 99.0]

_logger = logging.getLogger(__name__)

_SCHEMA = """
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
-- session_id index created post-migration; see _ensure_initialized.
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

-- v0.0.5 sessions table per design.md section 3.2.
-- One row per logical voice session. Populated by CostTracker on
-- the first request of a session; total_cost_usd and request_count
-- accumulate per request. ended_at stays NULL until the session
-- closes (session-close hook lands later in v0.0.5+).
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


class SQLiteStorage:
    """SQLite storage for request logs, costs, and latency metrics.

    Opens a fresh connection per call — no pooling, keeps things simple.
    Auto-migrates legacy schemas to add the `project` column.
    """

    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False

    async def _ensure_initialized(self) -> aiosqlite.Connection:
        db = await aiosqlite.connect(str(self._db_path))
        if not self._initialized:
            await db.executescript(_SCHEMA)
            # Migration: add `project` column if missing (from older schemas)
            cursor = await db.execute("PRAGMA table_info(requests)")
            cols = [row[1] async for row in cursor]
            if "project" not in cols:
                await db.execute(
                    "ALTER TABLE requests ADD COLUMN project TEXT NOT NULL DEFAULT 'default'"
                )
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_requests_project ON requests(project)"
                )
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_requests_project_timestamp "
                    "ON requests(project, timestamp)"
                )
            # Migration: add `pricing_source` column if missing (Phase 2.3 v0.1.0).
            # Pre-v0.1 rows get the empty-string default; new rows carry a real
            # value via CostTracker once Phase 2.3 #4 wires it through.
            if "pricing_source" not in cols:
                await db.execute(
                    "ALTER TABLE requests ADD COLUMN pricing_source TEXT NOT NULL DEFAULT ''"
                )
            # Migration: add `session_id` column if missing (v0.0.5).
            # Pre-v0.0.5 rows get NULL — correct, they predate the
            # session-correlation feature. New rows carry a real value
            # written by InstrumentedSTT/LLM/TTS once 5.6 #3 wires it through.
            if "session_id" not in cols:
                await db.execute(
                    "ALTER TABLE requests ADD COLUMN session_id TEXT"
                )
            # Always create the index (idempotent CREATE INDEX IF NOT EXISTS).
            # Lives outside the column-missing branch so a fresh install,
            # which already has the column from _SCHEMA, still gets the
            # index. Cannot live inside _SCHEMA itself because legacy
            # databases without the column yet would fail the CREATE.
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_requests_session_id "
                "ON requests(session_id)"
            )
            # Audit log table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS config_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    changes_json TEXT,
                    source TEXT NOT NULL DEFAULT 'api'
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_timestamp "
                "ON config_audit_log(timestamp)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_entity "
                "ON config_audit_log(entity_type, entity_id)"
            )

            # Migration: add `project` column to managed_providers if
            # missing (v0.0.5). NULL means "global / legacy scope" so
            # pre-v0.0.5 rows keep behaving exactly as before. New rows
            # written by vg_add_provider can carry a non-null project
            # name. The index lives outside the column-missing branch so
            # both fresh and migrated installs end up with it.
            mp_cursor = await db.execute("PRAGMA table_info(managed_providers)")
            mp_cols = [row[1] async for row in mp_cursor]
            if "project" not in mp_cols:
                await db.execute(
                    "ALTER TABLE managed_providers ADD COLUMN project TEXT"
                )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_managed_providers_project "
                "ON managed_providers(project)"
            )

            # Indexes on managed tables
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_managed_providers_type "
                "ON managed_providers(provider_type)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_managed_models_modality "
                "ON managed_models(modality)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_managed_models_provider "
                "ON managed_models(provider_id)"
            )

            # Migrate plaintext API keys to encrypted
            await self._migrate_plaintext_keys(db)

            await db.commit()
            self._initialized = True
        return db

    async def _migrate_plaintext_keys(self, db: aiosqlite.Connection) -> None:
        """Encrypt any plaintext API keys left over from before encryption was added."""
        from voicegateway.core.crypto import encrypt, is_fernet_token

        cursor = await db.execute(
            "SELECT provider_id, api_key_encrypted FROM managed_providers "
            "WHERE api_key_encrypted != ''"
        )
        rows = await cursor.fetchall()
        migrated = 0
        for row in rows:
            provider_id, raw_key = row[0], row[1]
            if not is_fernet_token(raw_key):
                encrypted = encrypt(raw_key)
                await db.execute(
                    "UPDATE managed_providers SET api_key_encrypted = ? WHERE provider_id = ?",
                    (encrypted, provider_id),
                )
                migrated += 1
        if migrated:
            _logger.warning(
                "Migrated %d plaintext API key(s) to encrypted storage.", migrated
            )

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    async def log_audit_event(
        self,
        entity_type: str,
        entity_id: str,
        action: str,
        changes: dict[str, Any] | None = None,
        source: str = "api",
    ) -> None:
        """Write an entry to the config_audit_log table. Best-effort — never raises."""
        db = await self._ensure_initialized()
        try:
            await db.execute(
                "INSERT INTO config_audit_log (timestamp, entity_type, entity_id, "
                "action, changes_json, source) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    time.time(),
                    entity_type,
                    entity_id,
                    action,
                    json.dumps(changes) if changes else None,
                    source,
                ),
            )
            await db.commit()
        except Exception:  # noqa: BLE001
            _logger.warning(
                "Failed to write audit log for %s/%s action=%s",
                entity_type,
                entity_id,
                action,
                exc_info=True,
            )
        finally:
            await db.close()

    async def get_audit_log(
        self,
        limit: int = 50,
        entity_type: str | None = None,
        entity_id: str | None = None,
        action: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return recent audit log entries."""
        db = await self._ensure_initialized()
        try:
            conditions: list[str] = []
            params: list[Any] = []
            if entity_type:
                conditions.append("entity_type = ?")
                params.append(entity_type)
            if entity_id:
                conditions.append("entity_id = ?")
                params.append(entity_id)
            if action:
                conditions.append("action = ?")
                params.append(action)
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            query = f"SELECT id, timestamp, entity_type, entity_id, action, changes_json, source FROM config_audit_log {where} ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            cursor = await db.execute(query, tuple(params))
            rows = []
            async for row in cursor:
                rows.append(
                    {
                        "id": row[0],
                        "timestamp": row[1],
                        "entity_type": row[2],
                        "entity_id": row[3],
                        "action": row[4],
                        "changes": json.loads(row[5]) if row[5] else None,
                        "source": row[6],
                    }
                )
            return rows
        finally:
            await db.close()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def log_request(self, record: RequestRecord) -> None:
        """Log a request record to the database.

        When ``record.session_id`` is set, this also upserts the matching
        row in the ``sessions`` table: starts the session on first
        request, accumulates total_cost_usd and request_count on each
        subsequent request, and adds the modality to the comma-separated
        modalities list (without duplication). Both writes happen on the
        same connection / commit for atomicity.
        """
        db = await self._ensure_initialized()
        try:
            await db.execute(
                """INSERT INTO requests
                   (id, timestamp, project, modality, model_id, provider,
                    input_units, output_units, cost_usd, pricing_source,
                    ttfb_ms, total_latency_ms, status,
                    fallback_from, error_message, metadata, session_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.timestamp,
                    record.project,
                    record.modality,
                    record.model_id,
                    record.provider,
                    record.input_units,
                    record.output_units,
                    record.cost_usd,
                    record.pricing_source,
                    record.ttfb_ms,
                    record.total_latency_ms,
                    record.status,
                    record.fallback_from,
                    record.error_message,
                    json.dumps(record.metadata) if record.metadata else None,
                    record.session_id,
                ),
            )
            if record.session_id:
                started_at_iso = _dt.datetime.fromtimestamp(
                    record.timestamp, tz=_dt.UTC
                ).isoformat()
                # SQL-side upsert: on conflict, accumulate cost / count
                # and union the modality into the comma-separated list
                # without duplicates. INSTR on bracketed keys (",stt,")
                # avoids substring false-positives like "tt" matching
                # in "stt". ended_at is set to the request's timestamp
                # on insert and bumped on every conflict so a session
                # row always reflects last-activity time without
                # requiring a session-close hook. Out-of-order arrivals
                # cannot drag ended_at backwards (CASE guards it).
                await db.execute(
                    """INSERT INTO sessions
                       (id, project, started_at, ended_at, modalities,
                        total_cost_usd, request_count)
                       VALUES (?, ?, ?, ?, ?, ?, 1)
                       ON CONFLICT(id) DO UPDATE SET
                           total_cost_usd = total_cost_usd + excluded.total_cost_usd,
                           request_count = request_count + 1,
                           ended_at = CASE
                               WHEN ended_at IS NULL THEN excluded.ended_at
                               WHEN ended_at < excluded.ended_at THEN excluded.ended_at
                               ELSE ended_at
                           END,
                           modalities = CASE
                               WHEN modalities = '' THEN excluded.modalities
                               WHEN INSTR(
                                   ',' || modalities || ',',
                                   ',' || excluded.modalities || ','
                               ) > 0 THEN modalities
                               ELSE modalities || ',' || excluded.modalities
                           END""",
                    (
                        record.session_id,
                        record.project,
                        started_at_iso,
                        started_at_iso,
                        record.modality,
                        record.cost_usd,
                    ),
                )
            await db.commit()
        finally:
            await db.close()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    @staticmethod
    def _period_since(period: str) -> float:
        now = time.time()
        if period == "today":
            return now - 86400
        if period == "week":
            return now - 7 * 86400
        if period == "month":
            return now - 30 * 86400
        return 0

    @staticmethod
    def _resolve_window(
        period: str = "today",
        start_ts: float | None = None,
        end_ts: float | None = None,
    ) -> tuple[float, float | None]:
        """Resolve a query window into a `(since, until)` timestamp pair.

        When either `start_ts` or `end_ts` is provided, the explicit
        bounds win and `period` is ignored. Missing bound defaults to
        unbounded on that side (``since=0`` / ``until=None``).

        When neither is provided, falls back to the named-period semantics
        via ``_period_since`` and returns ``until=None`` (no upper bound),
        preserving existing call sites' behavior.
        """
        if start_ts is not None or end_ts is not None:
            return (start_ts if start_ts is not None else 0.0, end_ts)
        return (SQLiteStorage._period_since(period), None)

    async def get_cost_summary(
        self,
        period: str = "today",
        project: str | None = None,
        include_pricing_source: bool = False,
        start_ts: float | None = None,
        end_ts: float | None = None,
    ) -> dict[str, Any]:
        """Get cost summary for the given period, optionally filtered by project.

        With ``include_pricing_source=True``, each entry in ``by_model``
        gains a ``pricing_source`` field carrying the catalog (or
        catalogs, comma-joined) that priced that model's requests.

        Pass `start_ts` and/or `end_ts` (POSIX timestamps) to override
        the named-period window. When either is set, `period` is
        ignored. `start_ts` is inclusive, `end_ts` is exclusive.
        """
        db = await self._ensure_initialized()
        try:
            since, until = self._resolve_window(period, start_ts, end_ts)

            params: list[Any] = [since]
            where = "WHERE timestamp >= ?"
            if until is not None:
                where += " AND timestamp < ?"
                params.append(until)
            if project:
                where += " AND project = ?"
                params.append(project)

            # Total cost
            cursor = await db.execute(
                f"SELECT COALESCE(SUM(cost_usd), 0) FROM requests {where}",
                tuple(params),
            )
            row = await cursor.fetchone()
            total = row[0] if row else 0.0

            # By provider
            cursor = await db.execute(
                f"""SELECT provider, SUM(cost_usd) as cost, COUNT(*) as count
                    FROM requests {where}
                    GROUP BY provider ORDER BY cost DESC""",
                tuple(params),
            )
            by_provider = {
                row[0]: {"cost": row[1], "requests": row[2]} async for row in cursor
            }

            # By model
            if include_pricing_source:
                cursor = await db.execute(
                    f"""SELECT model_id, SUM(cost_usd) as cost, COUNT(*) as count,
                               GROUP_CONCAT(DISTINCT pricing_source) as sources
                        FROM requests {where}
                        GROUP BY model_id ORDER BY cost DESC""",
                    tuple(params),
                )
                by_model = {
                    row[0]: {
                        "cost": row[1],
                        "requests": row[2],
                        "pricing_source": row[3] or "",
                    }
                    async for row in cursor
                }
            else:
                cursor = await db.execute(
                    f"""SELECT model_id, SUM(cost_usd) as cost, COUNT(*) as count
                        FROM requests {where}
                        GROUP BY model_id ORDER BY cost DESC""",
                    tuple(params),
                )
                by_model = {
                    row[0]: {"cost": row[1], "requests": row[2]}
                    async for row in cursor
                }

            return {
                "period": period,
                "project": project,
                "total": total,
                "by_provider": by_provider,
                "by_model": by_model,
            }
        finally:
            await db.close()

    async def get_cost_by_project(
        self,
        period: str = "today",
        start_ts: float | None = None,
        end_ts: float | None = None,
    ) -> dict[str, Any]:
        """Get cost summary grouped by project."""
        db = await self._ensure_initialized()
        try:
            since, until = self._resolve_window(period, start_ts, end_ts)
            params: list[Any] = [since]
            where = "WHERE timestamp >= ?"
            if until is not None:
                where += " AND timestamp < ?"
                params.append(until)
            cursor = await db.execute(
                f"""SELECT project, SUM(cost_usd) as cost, COUNT(*) as count
                    FROM requests {where}
                    GROUP BY project ORDER BY cost DESC""",
                tuple(params),
            )
            return {
                row[0]: {"cost": row[1], "requests": row[2]} async for row in cursor
            }
        finally:
            await db.close()

    async def get_cost_by_modality(
        self,
        period: str = "today",
        project: str | None = None,
        start_ts: float | None = None,
        end_ts: float | None = None,
    ) -> dict[str, Any]:
        """Get cost summary grouped by modality (stt/llm/tts).

        Returns a dict keyed by modality. Modalities with no requests in
        the period are absent from the result; callers that want a
        zero-filled view should overlay onto a {"stt": {...}, "llm":
        {...}, "tts": {...}} template.
        """
        db = await self._ensure_initialized()
        try:
            since, until = self._resolve_window(period, start_ts, end_ts)
            params: list[Any] = [since]
            where = "WHERE timestamp >= ?"
            if until is not None:
                where += " AND timestamp < ?"
                params.append(until)
            if project:
                where += " AND project = ?"
                params.append(project)
            cursor = await db.execute(
                f"""SELECT modality, SUM(cost_usd) as cost, COUNT(*) as count
                    FROM requests {where}
                    GROUP BY modality ORDER BY cost DESC""",
                tuple(params),
            )
            return {
                row[0]: {"cost": row[1], "requests": row[2]} async for row in cursor
            }
        finally:
            await db.close()

    async def get_latency_stats(
        self,
        period: str = "today",
        project: str | None = None,
        percentiles: list[float] | None = None,
    ) -> dict[str, Any]:
        """Get per-model latency stats for ``period``.

        Each entry contains the existing averages (``avg_ttfb_ms``,
        ``avg_latency_ms``, ``request_count``) plus nested
        ``ttfb_percentiles`` and ``latency_percentiles`` dicts keyed by
        ``p50`` / ``p95`` / ``p99`` (or whatever ``percentiles`` asks
        for). Models appear if they have *any* latency sample (either
        TTFB or total) — the averages and percentiles are computed
        independently, so a model with only ``total_latency_ms`` still
        gets ``latency_percentiles`` populated.

        With fewer than two samples for a particular metric, that
        metric's percentiles mirror the single value — see
        ``compute_percentiles`` for edge cases.
        """
        pcts = percentiles or _DEFAULT_PERCENTILES
        db = await self._ensure_initialized()
        try:
            since = self._period_since(period)
            params: list[Any] = [since]
            # Include any row with at least one latency metric so models
            # with only total_latency_ms aren't silently dropped.
            where = (
                "WHERE timestamp >= ? "
                "AND (ttfb_ms IS NOT NULL OR total_latency_ms IS NOT NULL)"
            )
            if project:
                where += " AND project = ?"
                params.append(project)

            cursor = await db.execute(
                f"""SELECT model_id,
                           AVG(ttfb_ms) as avg_ttfb,
                           AVG(total_latency_ms) as avg_latency,
                           COUNT(*) as count
                    FROM requests
                    {where}
                    GROUP BY model_id""",
                tuple(params),
            )
            stats: dict[str, dict[str, Any]] = {}
            async for row in cursor:
                stats[row[0]] = {
                    "avg_ttfb_ms": row[1],
                    "avg_latency_ms": row[2],
                    "request_count": row[3],
                    "ttfb_percentiles": compute_percentiles([], pcts),
                    "latency_percentiles": compute_percentiles([], pcts),
                }
            await cursor.close()

            if not stats:
                return stats

            sample_cursor = await db.execute(
                f"""SELECT model_id, ttfb_ms, total_latency_ms
                    FROM requests
                    {where}""",
                tuple(params),
            )
            ttfb_by_model: dict[str, list[float]] = {}
            lat_by_model: dict[str, list[float]] = {}
            async for row in sample_cursor:
                model_id = row[0]
                if row[1] is not None:
                    ttfb_by_model.setdefault(model_id, []).append(float(row[1]))
                if row[2] is not None:
                    lat_by_model.setdefault(model_id, []).append(float(row[2]))
            await sample_cursor.close()

            for model_id, entry in stats.items():
                entry["ttfb_percentiles"] = compute_percentiles(
                    ttfb_by_model.get(model_id, []), pcts
                )
                entry["latency_percentiles"] = compute_percentiles(
                    lat_by_model.get(model_id, []), pcts
                )

            return stats
        finally:
            await db.close()

    async def get_latency_samples(
        self,
        period: str = "today",
        project: str | None = None,
        modality: str | None = None,
    ) -> tuple[list[float], list[float]]:
        """Return ``(ttfb_samples, total_latency_samples)`` for ``period``.

        Used by callers that want overall (cross-model) percentiles —
        e.g. the MCP observability tool and the Prometheus summary
        lines. Rows with NULL latencies are omitted. ``modality``
        restricts samples to ``"stt"`` / ``"llm"`` / ``"tts"`` so the
        "overall" block in callers can reflect the same filter applied
        to their per-model view.
        """
        db = await self._ensure_initialized()
        try:
            since = self._period_since(period)
            params: list[Any] = [since]
            where = "WHERE timestamp >= ?"
            if project:
                where += " AND project = ?"
                params.append(project)
            if modality:
                where += " AND modality = ?"
                params.append(modality)

            cursor = await db.execute(
                f"""SELECT ttfb_ms, total_latency_ms
                    FROM requests
                    {where}""",
                tuple(params),
            )
            ttfb: list[float] = []
            total: list[float] = []
            async for row in cursor:
                if row[0] is not None:
                    ttfb.append(float(row[0]))
                if row[1] is not None:
                    total.append(float(row[1]))
            return ttfb, total
        finally:
            await db.close()

    async def get_requests_in_window(
        self,
        start_ts: float | None = None,
        end_ts: float | None = None,
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return every request record falling in `[start_ts, end_ts)`.

        Differs from `get_recent_requests` in two ways: no row limit
        (intended for export, not display), and time window is the
        primary filter. `start_ts` is inclusive, `end_ts` is exclusive,
        either is optional. Project is an optional secondary filter.
        Rows are returned ordered by timestamp ascending so a CSV
        export reads chronologically.
        """
        db = await self._ensure_initialized()
        try:
            conditions: list[str] = []
            params: list[Any] = []
            if start_ts is not None:
                conditions.append("timestamp >= ?")
                params.append(start_ts)
            if end_ts is not None:
                conditions.append("timestamp < ?")
                params.append(end_ts)
            if project:
                conditions.append("project = ?")
                params.append(project)
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            query = f"SELECT * FROM requests {where} ORDER BY timestamp ASC"
            cursor = await db.execute(query, tuple(params))
            columns = [d[0] for d in cursor.description]
            rows = []
            async for row in cursor:
                record = dict(zip(columns, row, strict=False))
                if record.get("metadata"):
                    try:
                        record["metadata"] = json.loads(record["metadata"])
                    except (ValueError, TypeError):
                        pass
                rows.append(record)
            return rows
        finally:
            await db.close()

    async def get_recent_requests(
        self,
        limit: int = 100,
        modality: str | None = None,
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get recent request records, optionally filtered by modality and/or project."""
        db = await self._ensure_initialized()
        try:
            conditions: list[str] = []
            params: list[Any] = []
            if modality:
                conditions.append("modality = ?")
                params.append(modality)
            if project:
                conditions.append("project = ?")
                params.append(project)
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            query = f"SELECT * FROM requests {where} ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            cursor = await db.execute(query, tuple(params))
            columns = [d[0] for d in cursor.description]
            rows = []
            async for row in cursor:
                record = dict(zip(columns, row, strict=False))
                if record.get("metadata"):
                    try:
                        record["metadata"] = json.loads(record["metadata"])
                    except (ValueError, TypeError):
                        pass
                rows.append(record)
            return rows
        finally:
            await db.close()

    async def get_project_stats(self, project: str) -> dict[str, Any]:
        """Get today's stats for a single project."""
        db = await self._ensure_initialized()
        try:
            since_today = self._period_since("today")
            cursor = await db.execute(
                """SELECT
                       COUNT(*),
                       COALESCE(SUM(cost_usd), 0),
                       AVG(ttfb_ms),
                       AVG(total_latency_ms)
                   FROM requests
                   WHERE project = ? AND timestamp >= ?""",
                (project, since_today),
            )
            row = await cursor.fetchone()
            if row is None:
                return {
                    "project": project,
                    "requests_today": 0,
                    "cost_today": 0.0,
                    "avg_ttfb_ms": None,
                    "avg_latency_ms": None,
                }
            return {
                "project": project,
                "requests_today": int(row[0] or 0),
                "cost_today": float(row[1] or 0.0),
                "avg_ttfb_ms": float(row[2]) if row[2] is not None else None,
                "avg_latency_ms": float(row[3]) if row[3] is not None else None,
            }
        finally:
            await db.close()

    # ------------------------------------------------------------------
    # Sessions (v0.0.5)
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_session(row: Any) -> dict[str, Any]:
        return {
            "id": row[0],
            "project": row[1],
            "started_at": row[2],
            "ended_at": row[3],
            "modalities": row[4].split(",") if row[4] else [],
            "total_cost_usd": float(row[5] or 0.0),
            "request_count": int(row[6] or 0),
        }

    _SESSION_ORDER_CLAUSES: dict[str, str] = {
        "started_at_desc": "started_at DESC",
        "started_at_asc": "started_at ASC",
        "cost_desc": "total_cost_usd DESC, started_at DESC",
        "cost_asc": "total_cost_usd ASC, started_at DESC",
    }

    async def list_sessions(
        self,
        limit: int = 100,
        project: str | None = None,
        order_by: str = "started_at_desc",
    ) -> list[dict[str, Any]]:
        """Return recent sessions, ordered per ``order_by``.

        Args:
            limit: Max rows to return.
            project: Optional project filter.
            order_by: One of ``"started_at_desc"`` (default),
                ``"started_at_asc"``, ``"cost_desc"``, ``"cost_asc"``.
                Cost orderings break ties by started_at DESC so two
                $0 sessions still surface newest first.

        Raises:
            ValueError: When ``order_by`` is not one of the supported
                values. The whitelist guards against SQL injection
                via the user-supplied parameter.
        """
        clause = self._SESSION_ORDER_CLAUSES.get(order_by)
        if clause is None:
            supported = ", ".join(sorted(self._SESSION_ORDER_CLAUSES))
            raise ValueError(
                f"Unknown order_by {order_by!r}. Supported: {supported}."
            )
        db = await self._ensure_initialized()
        try:
            if project:
                cursor = await db.execute(
                    f"""SELECT id, project, started_at, ended_at, modalities,
                              total_cost_usd, request_count
                       FROM sessions
                       WHERE project = ?
                       ORDER BY {clause}
                       LIMIT ?""",
                    (project, limit),
                )
            else:
                cursor = await db.execute(
                    f"""SELECT id, project, started_at, ended_at, modalities,
                              total_cost_usd, request_count
                       FROM sessions
                       ORDER BY {clause}
                       LIMIT ?""",
                    (limit,),
                )
            return [self._row_to_session(row) async for row in cursor]
        finally:
            await db.close()

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Return a single session by id, or None if not found.

        The returned dict carries the row's stored fields plus a
        ``by_modality`` breakdown ({modality: {"cost", "request_count"}})
        and a deduplicated ``providers`` list. Both are computed from
        the ``requests`` table on read, joined on ``session_id``.
        """
        db = await self._ensure_initialized()
        try:
            cursor = await db.execute(
                """SELECT id, project, started_at, ended_at, modalities,
                          total_cost_usd, request_count
                   FROM sessions
                   WHERE id = ?""",
                (session_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            session = self._row_to_session(row)

            # Per-modality breakdown via a join on session_id. A
            # session with no matching requests (a stub row written
            # before the wrapper called log_request — should not
            # happen, but guard anyway) returns an empty breakdown.
            mod_cursor = await db.execute(
                """SELECT modality,
                          COALESCE(SUM(cost_usd), 0) AS cost,
                          COUNT(*) AS request_count
                   FROM requests
                   WHERE session_id = ?
                   GROUP BY modality""",
                (session_id,),
            )
            session["by_modality"] = {
                mod_row[0]: {
                    "cost": float(mod_row[1] or 0.0),
                    "request_count": int(mod_row[2] or 0),
                }
                async for mod_row in mod_cursor
            }

            # Distinct providers seen in this session, sorted for a
            # stable response order.
            prov_cursor = await db.execute(
                """SELECT DISTINCT provider
                   FROM requests
                   WHERE session_id = ?
                   ORDER BY provider""",
                (session_id,),
            )
            session["providers"] = [prov_row[0] async for prov_row in prov_cursor]
            return session
        finally:
            await db.close()

    # ------------------------------------------------------------------
    # Managed providers / models / projects
    # ------------------------------------------------------------------

    async def list_managed_providers(self) -> list[dict[str, Any]]:
        """Return managed providers. api_key_encrypted is ciphertext; callers must decrypt."""
        db = await self._ensure_initialized()
        try:
            cursor = await db.execute(
                "SELECT provider_id, provider_type, api_key_encrypted, base_url, "
                "extra_config, created_at, updated_at, project FROM managed_providers "
                "ORDER BY created_at ASC"
            )
            rows = []
            async for row in cursor:
                rows.append(
                    {
                        "provider_id": row[0],
                        "provider_type": row[1],
                        "api_key_encrypted": row[2],
                        "base_url": row[3],
                        "extra_config": json.loads(row[4] or "{}"),
                        "created_at": row[5],
                        "updated_at": row[6],
                        "project": row[7],
                    }
                )
            return rows
        finally:
            await db.close()

    async def get_managed_provider(self, provider_id: str) -> dict[str, Any] | None:
        """Return a managed provider. api_key_encrypted is ciphertext."""
        db = await self._ensure_initialized()
        try:
            cursor = await db.execute(
                "SELECT provider_id, provider_type, api_key_encrypted, base_url, "
                "extra_config, created_at, updated_at, project FROM managed_providers "
                "WHERE provider_id = ?",
                (provider_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return {
                "provider_id": row[0],
                "provider_type": row[1],
                "api_key_encrypted": row[2],
                "base_url": row[3],
                "extra_config": json.loads(row[4] or "{}"),
                "created_at": row[5],
                "updated_at": row[6],
                "project": row[7],
            }
        finally:
            await db.close()

    async def upsert_managed_provider(
        self,
        provider_id: str,
        provider_type: str,
        api_key: str,
        base_url: str | None = None,
        extra_config: dict[str, Any] | None = None,
        project: str | None = None,
    ) -> None:
        from voicegateway.core.crypto import encrypt

        db = await self._ensure_initialized()
        try:
            now = time.time()
            encrypted_key = encrypt(api_key)
            await db.execute(
                """INSERT INTO managed_providers
                       (provider_id, provider_type, api_key_encrypted, base_url,
                        extra_config, created_at, updated_at, project)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(provider_id) DO UPDATE SET
                       provider_type=excluded.provider_type,
                       api_key_encrypted=excluded.api_key_encrypted,
                       base_url=excluded.base_url,
                       extra_config=excluded.extra_config,
                       updated_at=excluded.updated_at,
                       project=excluded.project""",
                (
                    provider_id,
                    provider_type,
                    encrypted_key,
                    base_url,
                    json.dumps(extra_config or {}),
                    now,
                    now,
                    project,
                ),
            )
            await db.commit()
        finally:
            await db.close()

    async def delete_managed_provider(self, provider_id: str) -> bool:
        db = await self._ensure_initialized()
        try:
            cursor = await db.execute(
                "DELETE FROM managed_providers WHERE provider_id = ?", (provider_id,)
            )
            await db.commit()
            return (cursor.rowcount or 0) > 0
        finally:
            await db.close()

    # Managed models

    async def list_managed_models(self) -> list[dict[str, Any]]:
        db = await self._ensure_initialized()
        try:
            cursor = await db.execute(
                "SELECT model_id, modality, provider_id, model_name, display_name, "
                "default_language, default_voice, extra_config, enabled, "
                "created_at, updated_at FROM managed_models ORDER BY created_at ASC"
            )
            rows = []
            async for row in cursor:
                rows.append(
                    {
                        "model_id": row[0],
                        "modality": row[1],
                        "provider_id": row[2],
                        "model_name": row[3],
                        "display_name": row[4],
                        "default_language": row[5],
                        "default_voice": row[6],
                        "extra_config": json.loads(row[7] or "{}"),
                        "enabled": bool(row[8]),
                        "created_at": row[9],
                        "updated_at": row[10],
                    }
                )
            return rows
        finally:
            await db.close()

    async def get_managed_model(self, model_id: str) -> dict[str, Any] | None:
        for m in await self.list_managed_models():
            if m["model_id"] == model_id:
                return m
        return None

    async def upsert_managed_model(
        self,
        model_id: str,
        modality: str,
        provider_id: str,
        model_name: str,
        display_name: str | None = None,
        default_language: str | None = None,
        default_voice: str | None = None,
        extra_config: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> None:
        db = await self._ensure_initialized()
        try:
            now = time.time()
            await db.execute(
                """INSERT INTO managed_models
                       (model_id, modality, provider_id, model_name, display_name,
                        default_language, default_voice, extra_config, enabled,
                        created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(model_id) DO UPDATE SET
                       modality=excluded.modality,
                       provider_id=excluded.provider_id,
                       model_name=excluded.model_name,
                       display_name=excluded.display_name,
                       default_language=excluded.default_language,
                       default_voice=excluded.default_voice,
                       extra_config=excluded.extra_config,
                       enabled=excluded.enabled,
                       updated_at=excluded.updated_at""",
                (
                    model_id,
                    modality,
                    provider_id,
                    model_name,
                    display_name,
                    default_language,
                    default_voice,
                    json.dumps(extra_config or {}),
                    1 if enabled else 0,
                    now,
                    now,
                ),
            )
            await db.commit()
        finally:
            await db.close()

    async def delete_managed_model(self, model_id: str) -> bool:
        db = await self._ensure_initialized()
        try:
            cursor = await db.execute(
                "DELETE FROM managed_models WHERE model_id = ?", (model_id,)
            )
            await db.commit()
            return (cursor.rowcount or 0) > 0
        finally:
            await db.close()

    # Managed projects

    async def list_managed_projects(self) -> list[dict[str, Any]]:
        db = await self._ensure_initialized()
        try:
            cursor = await db.execute(
                "SELECT project_id, name, description, daily_budget, budget_action, "
                "default_stack, stt_model, llm_model, tts_model, tags, "
                "created_at, updated_at FROM managed_projects ORDER BY created_at ASC"
            )
            rows = []
            async for row in cursor:
                rows.append(
                    {
                        "project_id": row[0],
                        "name": row[1],
                        "description": row[2],
                        "daily_budget": row[3],
                        "budget_action": row[4],
                        "default_stack": row[5],
                        "stt_model": row[6],
                        "llm_model": row[7],
                        "tts_model": row[8],
                        "tags": json.loads(row[9] or "[]"),
                        "created_at": row[10],
                        "updated_at": row[11],
                    }
                )
            return rows
        finally:
            await db.close()

    async def get_managed_project(self, project_id: str) -> dict[str, Any] | None:
        for p in await self.list_managed_projects():
            if p["project_id"] == project_id:
                return p
        return None

    async def upsert_managed_project(
        self,
        project_id: str,
        name: str,
        description: str = "",
        daily_budget: float = 0.0,
        budget_action: str = "warn",
        default_stack: str | None = None,
        stt_model: str | None = None,
        llm_model: str | None = None,
        tts_model: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        db = await self._ensure_initialized()
        try:
            now = time.time()
            await db.execute(
                """INSERT INTO managed_projects
                       (project_id, name, description, daily_budget, budget_action,
                        default_stack, stt_model, llm_model, tts_model, tags,
                        created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(project_id) DO UPDATE SET
                       name=excluded.name,
                       description=excluded.description,
                       daily_budget=excluded.daily_budget,
                       budget_action=excluded.budget_action,
                       default_stack=excluded.default_stack,
                       stt_model=excluded.stt_model,
                       llm_model=excluded.llm_model,
                       tts_model=excluded.tts_model,
                       tags=excluded.tags,
                       updated_at=excluded.updated_at""",
                (
                    project_id,
                    name,
                    description,
                    daily_budget,
                    budget_action,
                    default_stack,
                    stt_model,
                    llm_model,
                    tts_model,
                    json.dumps(tags or []),
                    now,
                    now,
                ),
            )
            await db.commit()
        finally:
            await db.close()

    async def delete_managed_project(self, project_id: str) -> bool:
        db = await self._ensure_initialized()
        try:
            cursor = await db.execute(
                "DELETE FROM managed_projects WHERE project_id = ?", (project_id,)
            )
            await db.commit()
            return (cursor.rowcount or 0) > 0
        finally:
            await db.close()
