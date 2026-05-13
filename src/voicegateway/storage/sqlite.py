"""SQLite storage backend for request logs, projects, and cost tracking."""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import logging
import time
from importlib import import_module
from pathlib import Path
from typing import Any

import aiosqlite

from voicegateway.core.guardrail_policy import GuardrailPolicy
from voicegateway.inference._session_context import (
    current_guardrail_policy_snapshot,
    current_guardrails_bypassed,
    current_routing_decision,
    current_tenant,
)
from voicegateway.middleware.guardrails import guardrail_policy_json
from voicegateway.storage import guardrail_events_repo, replay_repo, turns_repo
from voicegateway.storage._percentiles import compute_percentiles
from voicegateway.storage.models import RequestRecord

_DEFAULT_PERCENTILES: list[float] = [50.0, 95.0, 99.0]


_migration_0003 = import_module(
    "voicegateway.storage.migrations.0003_turns_and_deadair"
)
_migration_0004 = import_module("voicegateway.storage.migrations.0004_replay_tables")
_migration_0005 = import_module(
    "voicegateway.storage.migrations.0005_tenant_attribution"
)
_migration_0006 = import_module(
    "voicegateway.storage.migrations.0006_routing_and_branding"
)
_migration_0007 = import_module("voicegateway.storage.migrations.0007_guardrails")

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
        self._open_connections: set[aiosqlite.Connection] = set()

    async def _ensure_initialized(self) -> aiosqlite.Connection:
        db = await aiosqlite.connect(str(self._db_path))
        self._track_connection(db)
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
                await db.execute("ALTER TABLE requests ADD COLUMN session_id TEXT")
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

            # v0.2.0 migration 0003: turns + dead_air_events tables,
            # session-aggregate columns. Idempotent.
            await _migration_0003.apply(db)

            # v0.3.0 migration 0004: replay event tables + sessions.replay_size_bytes.
            # Idempotent.
            await _migration_0004.apply(db)

            # v0.4.0 migration 0005: virtual_keys table + tenant_id columns
            # on sessions/requests (unconditionally) and on the v0.2.0/v0.3.0
            # derived tables (conditionally via sqlite_master presence check
            # per OQ4). Idempotent.
            await _migration_0005.apply(db)

            # v0.5.0 migration 0006: routing columns on sessions
            # (budget_ms / budget_ms_used / budget_overrun / routed_llm /
            # routed_tts), branding_json on managed_projects (conditional),
            # and the new latency_observations table. Idempotent.
            await _migration_0006.apply(db)

            # v0.6.0 migration 0007: guardrail policy snapshots and
            # audit events. Idempotent.
            await _migration_0007.apply(db)

            await db.commit()
            self._initialized = True
        return db

    def _track_connection(self, db: aiosqlite.Connection) -> None:
        """Track raw handles so owner lifecycles can clean up stragglers."""
        self._open_connections.add(db)
        original_close = db.close

        async def _tracked_close() -> None:
            try:
                await original_close()
            finally:
                self._open_connections.discard(db)

        db.close = _tracked_close  # type: ignore[method-assign]

    async def aclose(self) -> None:
        """Close any raw connections still owned by this storage instance."""
        for db in list(self._open_connections):
            try:
                await db.close()
            except Exception:
                self._open_connections.discard(db)

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
        request_tenant_id = current_tenant()
        try:
            await db.execute(
                """INSERT INTO requests
                   (id, timestamp, project, modality, model_id, provider,
                    input_units, output_units, cost_usd, pricing_source,
                    ttfb_ms, total_latency_ms, status,
                    fallback_from, error_message, metadata, session_id,
                    tenant_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    request_tenant_id,
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
                # started_at is symmetrically guarded: requests are
                # logged on completion, so an STT call that started
                # earlier may finish after a faster LLM call. The MIN
                # CASE keeps started_at at the earliest request_started
                # timestamp regardless of completion order.
                # ``request_tenant_id`` (read once at the top from the
                # v0.4.0 tenant_id_ctx ContextVar, REQ-VG-TENANT-001) is
                # reused for both the requests row and the sessions
                # UPSERT. On INSERT it stamps the session row; on
                # CONFLICT we COALESCE so an already-set tenant_id
                # never gets overwritten by a later request that
                # arrived without a tenant in scope. This is the
                # "session has one tenant for its lifetime" rule from
                # the Refinery: the first tenant-bearing request wins,
                # subsequent unattributed-bucket requests don't clear
                # it.
                # v0.5.0: stamp the router's pick on the session row.
                # AC-5 guarantees the triple is immutable for the
                # session's lifetime, so COALESCE on conflict keeps the
                # first-set values and ignores later sessions writes
                # that arrive without a routing decision in scope.
                routing = current_routing_decision()
                r_llm: str | None
                r_tts: str | None
                r_budget: int | None
                r_overrun: int | None
                if routing is not None:
                    r_llm = routing[1]
                    r_tts = routing[2]
                    r_budget = routing[3]
                    r_overrun = 1 if routing[4] else 0
                else:
                    r_llm = None
                    r_tts = None
                    r_budget = None
                    r_overrun = None
                guardrail_snapshot = current_guardrail_policy_snapshot()
                if guardrail_snapshot is not None:
                    guardrail_policy = GuardrailPolicy.from_raw(guardrail_snapshot)
                    guardrails_active = 1 if guardrail_policy.is_active else 0
                    guardrails_bypassed = (
                        1
                        if guardrail_policy.is_active and current_guardrails_bypassed()
                        else 0
                    )
                    guardrail_snapshot_json = guardrail_policy_json(guardrail_policy)
                else:
                    guardrails_active = None
                    guardrails_bypassed = None
                    guardrail_snapshot_json = None
                await db.execute(
                    """INSERT INTO sessions
                       (id, project, started_at, ended_at, modalities,
                        total_cost_usd, request_count, tenant_id,
                        routed_llm, routed_tts, budget_ms, budget_overrun,
                        guardrails_active, guardrails_bypassed,
                        guardrail_policy_snapshot_json)
                       VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                           total_cost_usd = total_cost_usd + excluded.total_cost_usd,
                           request_count = request_count + 1,
                           tenant_id = COALESCE(tenant_id, excluded.tenant_id),
                           routed_llm = COALESCE(routed_llm, excluded.routed_llm),
                           routed_tts = COALESCE(routed_tts, excluded.routed_tts),
                           budget_ms = COALESCE(budget_ms, excluded.budget_ms),
                           budget_overrun = COALESCE(budget_overrun, excluded.budget_overrun),
                           guardrails_active = COALESCE(guardrails_active, excluded.guardrails_active),
                           guardrails_bypassed = COALESCE(guardrails_bypassed, excluded.guardrails_bypassed),
                           guardrail_policy_snapshot_json = COALESCE(guardrail_policy_snapshot_json, excluded.guardrail_policy_snapshot_json),
                           started_at = CASE
                               WHEN started_at IS NULL THEN excluded.started_at
                               WHEN started_at > excluded.started_at THEN excluded.started_at
                               ELSE started_at
                           END,
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
                        request_tenant_id,
                        r_llm,
                        r_tts,
                        r_budget,
                        r_overrun,
                        guardrails_active,
                        guardrails_bypassed,
                        guardrail_snapshot_json,
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
        tenant: str | None = None,
    ) -> dict[str, Any]:
        """Get cost summary for the given period, optionally filtered by project and tenant.

        With ``include_pricing_source=True``, each entry in ``by_model``
        gains a ``pricing_source`` field carrying the catalog (or
        catalogs, comma-joined) that priced that model's requests.

        Pass `start_ts` and/or `end_ts` (POSIX timestamps) to override
        the named-period window. When either is set, `period` is
        ignored. `start_ts` is inclusive, `end_ts` is exclusive.

        ``tenant`` (v0.4.0) scopes the aggregate to a single tenant.
        Pass an empty string ``""`` to target the unattributed bucket
        (sessions with NULL tenant_id). Pass ``None`` to include every
        tenant (default).
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
            if tenant is not None:
                if tenant == "":
                    where += " AND tenant_id IS NULL"
                else:
                    where += " AND tenant_id = ?"
                    params.append(tenant)

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
                    row[0]: {"cost": row[1], "requests": row[2]} async for row in cursor
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
        tenant: str | None = None,
    ) -> dict[str, Any]:
        """Get cost summary grouped by project.

        ``tenant`` (v0.4.0) scopes the result to one tenant; ``""``
        targets the unattributed bucket; ``None`` includes everything.
        """
        db = await self._ensure_initialized()
        try:
            since, until = self._resolve_window(period, start_ts, end_ts)
            params: list[Any] = [since]
            where = "WHERE timestamp >= ?"
            if until is not None:
                where += " AND timestamp < ?"
                params.append(until)
            if tenant is not None:
                if tenant == "":
                    where += " AND tenant_id IS NULL"
                else:
                    where += " AND tenant_id = ?"
                    params.append(tenant)
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
        tenant: str | None = None,
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
            if tenant is not None:
                if tenant == "":
                    where += " AND tenant_id IS NULL"
                else:
                    where += " AND tenant_id = ?"
                    params.append(tenant)

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
        tenant: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get recent request records, optionally filtered by modality, project, tenant.

        ``tenant`` (v0.4.0) accepts a tenant id; ``""`` targets the
        unattributed bucket; ``None`` includes everything.
        """
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
            if tenant is not None:
                if tenant == "":
                    conditions.append("tenant_id IS NULL")
                else:
                    conditions.append("tenant_id = ?")
                    params.append(tenant)
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
        out = {
            "id": row[0],
            "project": row[1],
            "started_at": row[2],
            "ended_at": row[3],
            "modalities": row[4].split(",") if row[4] else [],
            "total_cost_usd": float(row[5] or 0.0),
            "request_count": int(row[6] or 0),
        }
        # v0.4.0: include tenant_id when the SELECT picked it up.
        # v0.5.0: include the four routing columns the same way.
        # v0.6.0: include guardrail session flags/policy snapshot.
        # The Any-typed Row may not raise on out-of-bounds; guard
        # defensively so a SELECT that omits the columns (e.g. an
        # external caller reading the legacy seven-column shape)
        # still works.
        try:
            tenant_id = row[7]
            out["tenant_id"] = None if tenant_id is None else str(tenant_id)
        except (IndexError, KeyError):
            pass
        try:
            routed_llm = row[8]
            routed_tts = row[9]
            budget_ms = row[10]
            budget_overrun = row[11]
            out["routed_llm"] = None if routed_llm is None else str(routed_llm)
            out["routed_tts"] = None if routed_tts is None else str(routed_tts)
            out["budget_ms"] = None if budget_ms is None else int(budget_ms)
            out["budget_overrun"] = (
                None if budget_overrun is None else bool(budget_overrun)
            )
        except (IndexError, KeyError):
            pass
        try:
            guardrails_active = row[12]
            guardrails_bypassed = row[13]
            policy_snapshot = row[14]
            out["guardrails_active"] = (
                None if guardrails_active is None else bool(guardrails_active)
            )
            out["guardrails_bypassed"] = (
                None if guardrails_bypassed is None else bool(guardrails_bypassed)
            )
            out["guardrail_policy_snapshot"] = (
                json.loads(policy_snapshot) if policy_snapshot else None
            )
        except (IndexError, KeyError, TypeError, ValueError):
            pass
        return out

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
        tenant: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return recent sessions, ordered per ``order_by``.

        Args:
            limit: Max rows to return.
            project: Optional project filter.
            order_by: One of ``"started_at_desc"`` (default),
                ``"started_at_asc"``, ``"cost_desc"``, ``"cost_asc"``.
                Cost orderings break ties by started_at DESC so two
                $0 sessions still surface newest first.
            tenant: v0.4.0 tenant filter. Pass an empty string to
                target the unattributed bucket; ``None`` lists every
                tenant.

        Raises:
            ValueError: When ``order_by`` is not one of the supported
                values. The whitelist guards against SQL injection
                via the user-supplied parameter.
        """
        clause = self._SESSION_ORDER_CLAUSES.get(order_by)
        if clause is None:
            supported = ", ".join(sorted(self._SESSION_ORDER_CLAUSES))
            raise ValueError(f"Unknown order_by {order_by!r}. Supported: {supported}.")
        db = await self._ensure_initialized()
        try:
            conditions: list[str] = []
            params: list[Any] = []
            if project:
                conditions.append("project = ?")
                params.append(project)
            if tenant is not None:
                if tenant == "":
                    conditions.append("tenant_id IS NULL")
                else:
                    conditions.append("tenant_id = ?")
                    params.append(tenant)
            where = f"WHERE {' AND '.join(conditions)} " if conditions else ""
            params.append(limit)
            cursor = await db.execute(
                f"""SELECT id, project, started_at, ended_at, modalities,
                          total_cost_usd, request_count, tenant_id,
                          routed_llm, routed_tts, budget_ms, budget_overrun,
                          guardrails_active, guardrails_bypassed,
                          guardrail_policy_snapshot_json
                   FROM sessions
                   {where}ORDER BY {clause}
                   LIMIT ?""",
                tuple(params),
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
                          total_cost_usd, request_count, tenant_id,
                          routed_llm, routed_tts, budget_ms, budget_overrun,
                          guardrails_active, guardrails_bypassed,
                          guardrail_policy_snapshot_json
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
            events = await guardrail_events_repo.list_events_by_session(db, session_id)
            session["guardrail_events"] = [
                dataclasses.asdict(event) for event in events
            ]
            return session
        finally:
            await db.close()

    async def finalize_session_metrics(self, session_id: str) -> None:
        """Recompute and upsert the v0.2.0 aggregate columns on a session row.

        Called by the cost_tracker session-close hook (T08) once the
        TurnTracker and DeadAirDetector have flushed their captures.
        Reads from the ``turns`` and ``dead_air_events`` tables and
        writes five aggregate columns to the ``sessions`` row:

        - ``talk_time_seconds`` -- sum of caller and agent speech
          durations across all turns, in seconds.
        - ``per_minute_cost_usd`` -- ``total_cost_usd /
          (talk_time_seconds / 60)``. NULL when talk_time is zero.
        - ``response_speed_p50_ms``, ``response_speed_p95_ms`` -- from
          :func:`voicegateway.storage.turns_repo.aggregate_response_speed`.
          (p99 is computed but not stored on the sessions row per Foundry.)
        - ``talk_over_rate`` -- ``overlap_count / total_turns``. NULL
          when total_turns is zero.

        Pre-v0.2.0 sessions that recorded no turns keep NULL on every
        new column (REQ-VG-METRICS-006). A session that recorded turns
        but ended with zero cost still gets ``per_minute_cost_usd =
        0.0``, not NULL — the metric is defined when talk_time > 0.

        Implements REQ-VG-METRICS-001 (per-minute cost) and feeds
        REQ-VG-METRICS-002, -003, -004, -006 via the same row.
        """
        db = await self._ensure_initialized()
        try:
            turns = await turns_repo.list_turns_by_session(db, session_id)
            if not turns:
                # No turn data → leave aggregates NULL (REQ-006 contract).
                return

            talk_time_ms = 0
            for t in turns:
                talk_time_ms += t.caller_speak_end_ms - t.caller_speak_start_ms
                if (
                    t.agent_speak_start_ms is not None
                    and t.agent_speak_end_ms is not None
                ):
                    talk_time_ms += t.agent_speak_end_ms - t.agent_speak_start_ms
            talk_time_seconds = talk_time_ms / 1000.0

            cost_cursor = await db.execute(
                "SELECT total_cost_usd FROM sessions WHERE id = ?",
                (session_id,),
            )
            cost_row = await cost_cursor.fetchone()
            total_cost = (
                float(cost_row[0])
                if cost_row is not None and cost_row[0] is not None
                else 0.0
            )
            per_minute_cost = (
                total_cost / (talk_time_seconds / 60.0)
                if talk_time_seconds > 0
                else None
            )

            pcts = await turns_repo.aggregate_response_speed(db, session_id)
            overlap_count = await turns_repo.count_overlap_turns(db, session_id)
            total_turns = len(turns)
            talk_over_rate = overlap_count / total_turns if total_turns > 0 else None

            await db.execute(
                """UPDATE sessions
                      SET talk_time_seconds = ?,
                          per_minute_cost_usd = ?,
                          response_speed_p50_ms = ?,
                          response_speed_p95_ms = ?,
                          talk_over_rate = ?
                    WHERE id = ?""",
                (
                    talk_time_seconds,
                    per_minute_cost,
                    pcts["p50_ms"],
                    pcts["p95_ms"],
                    talk_over_rate,
                    session_id,
                ),
            )
            await db.commit()
        finally:
            await db.close()

    async def finalize_session_replay(self, session_id: str) -> None:
        """Compute ``replay_size_bytes`` for a session and upsert the row.

        Called by the cost_tracker session-close hook (T09) after the
        replay-capture buffer has flushed (T02's ReplayCapture flushes
        in its own session-close path). Reads the per-modality
        ``replay_*`` tables via
        :func:`voicegateway.storage.replay_repo.aggregate_storage_per_session`
        and writes the byte-length sum to the sessions row's
        ``replay_size_bytes`` column.

        NULL is preserved when the session captured no replay events
        (pre-v0.3.0 sessions per REQ-VG-REPLAY-006, or projects with
        ``replay.enabled = False`` per T07). The column is also NULL
        when the session row is missing entirely.
        """
        db = await self._ensure_initialized()
        try:
            size_bytes = await replay_repo.aggregate_storage_per_session(db, session_id)
            # Leave NULL for sessions that captured nothing — the
            # dashboard reads NULL as "not measured" the same way it
            # does for v0.2.0 aggregate columns.
            if size_bytes <= 0:
                return
            await db.execute(
                "UPDATE sessions SET replay_size_bytes = ? WHERE id = ?",
                (size_bytes, session_id),
            )
            await db.commit()
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

    async def rotate_managed_credentials(
        self, *, time_now: float | None = None
    ) -> dict[str, Any]:
        """Re-encrypt every managed_providers row under the current
        primary Fernet key.

        Used by ``voicegw rotate-secret`` after the operator sets a
        new ``VOICEGW_SECRET`` and the previous value as
        ``VOICEGW_SECRET_FALLBACK``. Each row's ``api_key_encrypted``
        is decrypted via MultiFernet (which tries primary then any
        fallbacks) and re-encrypted via the primary, then written
        back. The ``updated_at`` column is bumped so the dashboard
        and audit views show the rotation as a recent change.

        Empty ``api_key_encrypted`` columns (from rows that the user
        added without a key) are skipped — there is nothing to
        rotate. Rows whose ciphertext does not decrypt under any
        configured key are recorded in the returned ``failed`` list
        so the CLI can surface them to the operator without aborting
        the rotation halfway through.

        Returns:
            ``{"rotated": <int>, "skipped_empty": <int>,
              "failed": [<provider_id>, ...]}``
        """
        from voicegateway.core.crypto import rotate_token

        now = time_now if time_now is not None else time.time()
        rotated = 0
        skipped_empty = 0
        failed: list[str] = []

        db = await self._ensure_initialized()
        try:
            cursor = await db.execute(
                "SELECT provider_id, api_key_encrypted FROM managed_providers"
            )
            rows = await cursor.fetchall()
            for provider_id, ciphertext in rows:
                if not ciphertext:
                    skipped_empty += 1
                    continue
                try:
                    new_ciphertext = rotate_token(ciphertext)
                except ValueError:
                    failed.append(provider_id)
                    continue
                if new_ciphertext == ciphertext:
                    # MultiFernet.rotate is non-deterministic (Fernet
                    # uses a random IV), so this branch is effectively
                    # unreachable. Guard anyway: if it fires, we still
                    # bump updated_at so the dashboard reflects the
                    # rotation attempt.
                    pass
                await db.execute(
                    "UPDATE managed_providers SET api_key_encrypted = ?, "
                    "updated_at = ? WHERE provider_id = ?",
                    (new_ciphertext, now, provider_id),
                )
                rotated += 1
            await db.commit()
        finally:
            await db.close()

        return {"rotated": rotated, "skipped_empty": skipped_empty, "failed": failed}

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

    @staticmethod
    def _validate_branding(branding: dict[str, Any] | None) -> dict[str, Any] | None:
        """Validate the v0.5.0 branding payload before write.

        OQ4 + dashboard contract:
        - ``logo_url`` (optional): string up to 2048 chars.
        - ``accent_color`` (optional): hex string ``#RRGGBB`` or
          ``#RGB``.
        - ``product_name`` (optional): string up to 64 chars.

        Returns the validated dict (or ``None`` when input is None /
        empty). Raises ``ValueError`` on shape or constraint violations.
        """
        import re

        if branding is None:
            return None
        if not isinstance(branding, dict):
            raise ValueError(
                f"branding must be a dict or None, got {type(branding).__name__}"
            )
        if not branding:
            return None
        out: dict[str, Any] = {}
        allowed = {"logo_url", "accent_color", "product_name"}
        for key in branding:
            if key not in allowed:
                raise ValueError(
                    f"branding has unknown key {key!r}; allowed: {sorted(allowed)}"
                )
        logo_url = branding.get("logo_url")
        if logo_url is not None:
            if not isinstance(logo_url, str) or len(logo_url) > 2048:
                raise ValueError("branding.logo_url must be a string up to 2048 chars")
            out["logo_url"] = logo_url
        accent_color = branding.get("accent_color")
        if accent_color is not None:
            if not isinstance(accent_color, str) or not re.fullmatch(
                r"#[0-9A-Fa-f]{3}([0-9A-Fa-f]{3})?", accent_color
            ):
                raise ValueError(
                    "branding.accent_color must be a hex string (#RGB or #RRGGBB)"
                )
            out["accent_color"] = accent_color
        product_name = branding.get("product_name")
        if product_name is not None:
            if not isinstance(product_name, str) or len(product_name) > 64:
                raise ValueError(
                    "branding.product_name must be a string up to 64 chars"
                )
            out["product_name"] = product_name
        return out or None

    async def list_managed_projects(self) -> list[dict[str, Any]]:
        db = await self._ensure_initialized()
        try:
            cursor = await db.execute(
                "SELECT project_id, name, description, daily_budget, budget_action, "
                "default_stack, stt_model, llm_model, tts_model, tags, "
                "created_at, updated_at, branding_json, guardrail_policy_json "
                "FROM managed_projects ORDER BY created_at ASC"
            )
            rows = []
            async for row in cursor:
                branding_raw = row[12] if len(row) > 12 else None
                guardrail_raw = row[13] if len(row) > 13 else None
                branding = None
                if branding_raw:
                    try:
                        branding = json.loads(branding_raw)
                    except (ValueError, TypeError):
                        branding = None
                guardrail_policy = None
                if guardrail_raw:
                    try:
                        guardrail_policy = json.loads(guardrail_raw)
                    except (ValueError, TypeError):
                        guardrail_policy = None
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
                        "branding": branding,
                        "guardrail_policy": guardrail_policy,
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
        branding: dict[str, Any] | None = None,
        guardrail_policy: dict[str, Any] | None = None,
    ) -> None:
        validated_branding = self._validate_branding(branding)
        branding_json = json.dumps(validated_branding) if validated_branding else None
        validated_guardrails = (
            GuardrailPolicy.from_raw(guardrail_policy).to_storage_dict()
            if guardrail_policy is not None
            else None
        )
        guardrail_json = (
            json.dumps(validated_guardrails, sort_keys=True)
            if validated_guardrails is not None
            else None
        )
        db = await self._ensure_initialized()
        try:
            now = time.time()
            await db.execute(
                """INSERT INTO managed_projects
                       (project_id, name, description, daily_budget, budget_action,
                        default_stack, stt_model, llm_model, tts_model, tags,
                        created_at, updated_at, branding_json, guardrail_policy_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                       branding_json=COALESCE(excluded.branding_json, branding_json),
                       guardrail_policy_json=COALESCE(excluded.guardrail_policy_json, guardrail_policy_json),
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
                    branding_json,
                    guardrail_json,
                ),
            )
            await db.commit()
        finally:
            await db.close()

    async def set_managed_project_guardrails(
        self,
        *,
        project_id: str,
        policy: dict[str, Any] | None,
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
        """Set or clear a project's guardrail policy overlay."""
        guardrail_json = None
        if policy is not None:
            validated = GuardrailPolicy.from_raw(policy).to_storage_dict()
            guardrail_json = json.dumps(validated, sort_keys=True)
        db = await self._ensure_initialized()
        try:
            now = time.time()
            await db.execute(
                """INSERT INTO managed_projects
                       (project_id, name, description, daily_budget, budget_action,
                        default_stack, stt_model, llm_model, tts_model, tags,
                        created_at, updated_at, guardrail_policy_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(project_id) DO UPDATE SET
                       guardrail_policy_json=excluded.guardrail_policy_json,
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
                    guardrail_json,
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
