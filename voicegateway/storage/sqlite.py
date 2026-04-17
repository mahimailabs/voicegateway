"""SQLite storage backend for request logs, projects, and cost tracking."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

from voicegateway.storage.models import RequestRecord

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
    ttfb_ms REAL,
    total_latency_ms REAL,
    status TEXT DEFAULT 'success',
    fallback_from TEXT,
    error_message TEXT,
    metadata TEXT
);

CREATE INDEX IF NOT EXISTS idx_requests_timestamp ON requests(timestamp);
CREATE INDEX IF NOT EXISTS idx_requests_model ON requests(model_id);
CREATE INDEX IF NOT EXISTS idx_requests_modality ON requests(modality);
CREATE INDEX IF NOT EXISTS idx_requests_project ON requests(project);
CREATE INDEX IF NOT EXISTS idx_requests_project_timestamp ON requests(project, timestamp);

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
            await db.commit()
            self._initialized = True
        return db

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def log_request(self, record: RequestRecord) -> None:
        """Log a request record to the database."""
        db = await self._ensure_initialized()
        try:
            await db.execute(
                """INSERT INTO requests
                   (id, timestamp, project, modality, model_id, provider,
                    input_units, output_units, cost_usd,
                    ttfb_ms, total_latency_ms, status,
                    fallback_from, error_message, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    record.ttfb_ms,
                    record.total_latency_ms,
                    record.status,
                    record.fallback_from,
                    record.error_message,
                    json.dumps(record.metadata) if record.metadata else None,
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

    async def get_cost_summary(
        self, period: str = "today", project: str | None = None
    ) -> dict[str, Any]:
        """Get cost summary for the given period, optionally filtered by project."""
        db = await self._ensure_initialized()
        try:
            since = self._period_since(period)

            params: list[Any] = [since]
            where = "WHERE timestamp >= ?"
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
                row[0]: {"cost": row[1], "requests": row[2]}
                async for row in cursor
            }

            # By model
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

    async def get_cost_by_project(self, period: str = "today") -> dict[str, Any]:
        """Get cost summary grouped by project."""
        db = await self._ensure_initialized()
        try:
            since = self._period_since(period)
            cursor = await db.execute(
                """SELECT project, SUM(cost_usd) as cost, COUNT(*) as count
                   FROM requests WHERE timestamp >= ?
                   GROUP BY project ORDER BY cost DESC""",
                (since,),
            )
            return {
                row[0]: {"cost": row[1], "requests": row[2]}
                async for row in cursor
            }
        finally:
            await db.close()

    async def get_latency_stats(
        self, period: str = "today", project: str | None = None
    ) -> dict[str, Any]:
        """Get latency statistics for the given period, optionally filtered by project."""
        db = await self._ensure_initialized()
        try:
            since = self._period_since(period)
            params: list[Any] = [since]
            where = "WHERE timestamp >= ? AND ttfb_ms IS NOT NULL"
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
            stats = {}
            async for row in cursor:
                stats[row[0]] = {
                    "avg_ttfb_ms": row[1],
                    "avg_latency_ms": row[2],
                    "request_count": row[3],
                }
            return stats
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
