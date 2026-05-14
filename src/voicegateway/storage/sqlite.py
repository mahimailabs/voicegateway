"""SQLite storage backend for request logs, projects, and cost tracking."""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from pathlib import Path
from typing import Any

import aiosqlite

from voicegateway.models.request_model import RequestRecord
from voicegateway.repository import (
    guardrail_events_repository as guardrail_events,
)
from voicegateway.repository import (
    replay_repository as replay,
)
from voicegateway.repository import (
    turns_repository as turns,
)
from voicegateway.schemas.guardrail_policy_schema import GuardrailPolicy
from voicegateway.services.request_log_service import RequestLogService
from voicegateway.storage.connection import ConnectionManager
from voicegateway.storage.migrator import initialize as _initialize_schema
from voicegateway.utils.percentiles import compute_percentiles

_DEFAULT_PERCENTILES: list[float] = [50.0, 95.0, 99.0]

_logger = logging.getLogger(__name__)


class SQLiteStorage:
    """SQLite storage for request logs, costs, and latency metrics."""

    def __init__(self, db_path: str | Path) -> None:
        self._conn = ConnectionManager(db_path)
        self._request_log_service = RequestLogService(self._conn)

    @property
    def _db_path(self) -> Path:
        """Back-compat shim for callers reading the file path directly."""
        return self._conn.db_path

    @property
    def _initialized(self) -> bool:
        """Back-compat shim used by a few tests to assert post-init state."""
        return self._conn.is_initialized

    async def _ensure_initialized(self) -> aiosqlite.Connection:
        db = await self._conn.connect()
        await _initialize_schema(db, self._conn)
        return db

    async def aclose(self) -> None:
        """Close any raw connections still owned by this storage instance."""
        await self._conn.aclose()

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
        """Delegate to RequestLogService.log_audit_event."""
        await self._ensure_initialized()
        await self._request_log_service.log_audit_event(
            entity_type, entity_id, action, changes, source
        )

    async def get_audit_log(
        self,
        limit: int = 50,
        entity_type: str | None = None,
        entity_id: str | None = None,
        action: str | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate to RequestLogService.get_audit_log."""
        await self._ensure_initialized()
        return await self._request_log_service.get_audit_log(
            limit, entity_type, entity_id, action
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def log_request(self, record: RequestRecord) -> None:
        """Delegate to RequestLogService.log_request."""
        await self._ensure_initialized()
        await self._request_log_service.log_request(record)

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
        """Resolve a query window into a `(since, until)` timestamp pair."""
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
        """Get cost summary for the given period, optionally filtered by project and tenant."""
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
        """Get cost summary grouped by project."""
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
        """Get cost summary grouped by modality (stt/llm/tts)."""
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
        """Get per-model latency stats for ``period``."""
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
        """Return ``(ttfb_samples, total_latency_samples)`` for ``period``."""
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
        """Delegate to RequestLogService.get_requests_in_window."""
        await self._ensure_initialized()
        return await self._request_log_service.get_requests_in_window(
            start_ts, end_ts, project
        )

    async def get_recent_requests(
        self,
        limit: int = 100,
        modality: str | None = None,
        project: str | None = None,
        tenant: str | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate to RequestLogService.get_recent_requests."""
        await self._ensure_initialized()
        return await self._request_log_service.get_recent_requests(
            limit, modality, project, tenant
        )

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
    # Sessions
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
        # include tenant_id when the SELECT picked it up.
        # include the four routing columns the same way.
        # include guardrail session flags/policy snapshot.
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
        """Return recent sessions, ordered per ``order_by``."""
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
        """Return a single session by id, or None if not found."""
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
            events = await guardrail_events.list_events_by_session(db, session_id)
            session["guardrail_events"] = [
                dataclasses.asdict(event) for event in events
            ]
            return session
        finally:
            await db.close()

    async def finalize_session_metrics(self, session_id: str) -> None:
        """Recompute and upsert the aggregate columns on a session row."""
        db = await self._ensure_initialized()
        try:
            session_turns = await turns.list_turns_by_session(db, session_id)
            if not session_turns:
                # No turn data → leave aggregates NULL.
                return

            talk_time_ms = 0
            for t in session_turns:
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

            pcts = await turns.aggregate_response_speed(db, session_id)
            overlap_count = await turns.count_overlap_turns(db, session_id)
            total_turns = len(session_turns)
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
        """Compute ``replay_size_bytes`` for a session and upsert the row."""
        db = await self._ensure_initialized()
        try:
            size_bytes = await replay.aggregate_storage_per_session(db, session_id)
            # Leave NULL for sessions that captured nothing — the
            # dashboard reads NULL as "not measured" the same way it
            # does for the other session-aggregate columns.
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
        """Re-encrypt every managed_providers row under the current"""
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
        """Validate the branding payload before write."""
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
