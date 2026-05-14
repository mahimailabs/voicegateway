"""Async repo for request log writes + audit events + raw row reads."""

from __future__ import annotations

import datetime as _dt
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from voicegateway.inference.session.context import (
    current_guardrail_policy_snapshot,
    current_guardrails_bypassed,
    current_routing_decision,
    current_tenant,
)
from voicegateway.middleware.guardrails import guardrail_policy_json
from voicegateway.schemas.guardrail_policy_schema import GuardrailPolicy

if TYPE_CHECKING:
    import aiosqlite

    from voicegateway.models.request_model import RequestRecord

_logger = logging.getLogger(__name__)


async def log_request(db: aiosqlite.Connection, record: RequestRecord) -> None:
    """Insert one request row + accumulate the session row (UPSERT)."""
    request_tenant_id = current_tenant()
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
                1 if guardrail_policy.is_active and current_guardrails_bypassed() else 0
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


async def log_audit_event(
    db: aiosqlite.Connection,
    entity_type: str,
    entity_id: str,
    action: str,
    changes: dict[str, Any] | None = None,
    source: str = "api",
) -> None:
    """Best-effort write of one config_audit_log row. Never raises."""
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


async def get_audit_log(
    db: aiosqlite.Connection,
    limit: int = 50,
    entity_type: str | None = None,
    entity_id: str | None = None,
    action: str | None = None,
) -> list[dict[str, Any]]:
    """Return recent config_audit_log entries."""
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
    query = (
        "SELECT id, timestamp, entity_type, entity_id, action, "
        "changes_json, source FROM config_audit_log "
        f"{where} ORDER BY timestamp DESC LIMIT ?"
    )
    params.append(limit)

    cursor = await db.execute(query, tuple(params))
    rows: list[dict[str, Any]] = []
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


async def get_recent_requests(
    db: aiosqlite.Connection,
    limit: int = 100,
    modality: str | None = None,
    project: str | None = None,
    tenant: str | None = None,
) -> list[dict[str, Any]]:
    """Return the N newest request rows, optionally filtered."""
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
    rows: list[dict[str, Any]] = []
    async for row in cursor:
        record = dict(zip(columns, row, strict=False))
        if record.get("metadata"):
            try:
                record["metadata"] = json.loads(record["metadata"])
            except (ValueError, TypeError):
                pass
        rows.append(record)
    return rows


async def get_requests_in_window(
    db: aiosqlite.Connection,
    start_ts: float | None = None,
    end_ts: float | None = None,
    project: str | None = None,
) -> list[dict[str, Any]]:
    """Return every request row falling in ``[start_ts, end_ts)``."""
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
    rows: list[dict[str, Any]] = []
    async for row in cursor:
        record = dict(zip(columns, row, strict=False))
        if record.get("metadata"):
            try:
                record["metadata"] = json.loads(record["metadata"])
            except (ValueError, TypeError):
                pass
        rows.append(record)
    return rows


__all__ = [
    "get_audit_log",
    "get_recent_requests",
    "get_requests_in_window",
    "log_audit_event",
    "log_request",
]
