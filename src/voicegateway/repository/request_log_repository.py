"""Async repo for request log writes + audit events + raw row reads (ORM).

The sessions UPSERT stays as a ``text()`` clause: it carries the
INSTR-based modality CSV union, the started_at/ended_at min/max
preservation, and the COALESCE-vs-null preservation for the routing
and guardrail aggregate columns. These semantics are the algorithm;
converting to ORM ``on_conflict_do_update`` with chained CASE / INSTR
expressions would only obscure the intent.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from voicegateway.inference.session.context import (
    current_guardrail_policy_snapshot,
    current_guardrails_bypassed,
    current_routing_decision,
    current_tenant,
)
from voicegateway.schemas.guardrail_policy_schema import GuardrailPolicy
from voicegateway.services.guardrail_service import policy_to_json

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from voicegateway.models.request_model import RequestRecord

_logger = logging.getLogger(__name__)


_INSERT_REQUEST = text(
    """INSERT INTO requests
       (id, timestamp, project, modality, model_id, provider,
        input_units, output_units, cached_input_units, cost_usd, pricing_source,
        ttfb_ms, total_latency_ms, status,
        fallback_from, error_message, metadata, session_id,
        tenant_id, agent_id)
       VALUES (:id, :timestamp, :project, :modality, :model_id, :provider,
               :input_units, :output_units, :cached_input_units, :cost_usd, :pricing_source,
               :ttfb_ms, :total_latency_ms, :status,
               :fallback_from, :error_message, :metadata, :session_id,
               :tenant_id, :agent_id)"""
)


# Sessions UPSERT — preserved byte-for-byte from the legacy aiosqlite form.
# Carries the INSTR-based modality CSV union, the started_at/ended_at min/max
# preservation, and the COALESCE-vs-null preservation on the routing and
# guardrail aggregate columns. Translating to on_conflict_do_update would
# obscure these three invariants.
_UPSERT_SESSION = text(
    """INSERT INTO sessions
       (id, project, started_at, ended_at, modalities,
        total_cost_usd, request_count, tenant_id, agent_id,
        routed_llm, routed_tts, budget_ms, budget_overrun,
        guardrails_active, guardrails_bypassed,
        guardrail_policy_snapshot_json)
       VALUES (:id, :project, :started_at, :ended_at, :modalities,
               :cost, 1, :tenant_id, :agent_id,
               :routed_llm, :routed_tts, :budget_ms, :budget_overrun,
               :guardrails_active, :guardrails_bypassed,
               :guardrail_snapshot_json)
       ON CONFLICT(id) DO UPDATE SET
           total_cost_usd = total_cost_usd + excluded.total_cost_usd,
           request_count = request_count + 1,
           tenant_id = COALESCE(tenant_id, excluded.tenant_id),
           agent_id = COALESCE(agent_id, excluded.agent_id),
           routed_llm = COALESCE(routed_llm, excluded.routed_llm),
           routed_tts = COALESCE(routed_tts, excluded.routed_tts),
           budget_ms = COALESCE(budget_ms, excluded.budget_ms),
           budget_overrun = COALESCE(budget_overrun, excluded.budget_overrun),
           guardrails_active = COALESCE(
               guardrails_active, excluded.guardrails_active
           ),
           guardrails_bypassed = COALESCE(
               guardrails_bypassed, excluded.guardrails_bypassed
           ),
           guardrail_policy_snapshot_json = COALESCE(
               guardrail_policy_snapshot_json,
               excluded.guardrail_policy_snapshot_json
           ),
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
           END"""
)


# Postgres uses STRPOS where SQLite uses INSTR (identical (haystack, needle)
# signature and 1-based / 0-if-absent semantics); the rest of the UPSERT is
# portable. Derive the PG statement from the SQLite one so the two never drift.
_UPSERT_SESSION_PG = text(_UPSERT_SESSION.text.replace("INSTR(", "STRPOS("))

_UPSERT_SESSION_BY_DIALECT: dict[str, Any] = {
    "sqlite": _UPSERT_SESSION,
    "postgresql": _UPSERT_SESSION_PG,
}


def _session_upsert_stmt(session: AsyncSession) -> Any:
    """Pick the dialect-appropriate sessions UPSERT (defaults to SQLite)."""
    try:
        name = session.bind.dialect.name
    except Exception:  # noqa: BLE001
        name = "sqlite"
    return _UPSERT_SESSION_BY_DIALECT.get(name, _UPSERT_SESSION_BY_DIALECT["sqlite"])


async def log_request(session: AsyncSession, record: RequestRecord) -> None:
    """Insert one request row + accumulate the session row (UPSERT)."""
    request_tenant_id = current_tenant()
    await session.execute(
        _INSERT_REQUEST,
        {
            "id": record.id,
            "timestamp": record.timestamp,
            "project": record.project,
            "modality": record.modality,
            "model_id": record.model_id,
            "provider": record.provider,
            "input_units": record.input_units,
            "output_units": record.output_units,
            "cached_input_units": record.cached_input_units,
            "cost_usd": record.cost_usd,
            "pricing_source": record.pricing_source,
            "ttfb_ms": record.ttfb_ms,
            "total_latency_ms": record.total_latency_ms,
            "status": record.status,
            "fallback_from": record.fallback_from,
            "error_message": record.error_message,
            "metadata": json.dumps(record.metadata) if record.metadata else None,
            "session_id": record.session_id,
            "tenant_id": request_tenant_id,
            "agent_id": record.agent_id,
        },
    )
    if record.session_id:
        started_at_iso = _dt.datetime.fromtimestamp(
            record.timestamp, tz=_dt.UTC
        ).isoformat()
        routing = current_routing_decision()
        if routing is not None:
            r_llm: str | None = routing[1]
            r_tts: str | None = routing[2]
            r_budget: int | None = routing[3]
            r_overrun: int | None = 1 if routing[4] else 0
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
            guardrail_snapshot_json = policy_to_json(guardrail_policy)
        else:
            guardrails_active = None
            guardrails_bypassed = None
            guardrail_snapshot_json = None
        await session.execute(
            _session_upsert_stmt(session),
            {
                "id": record.session_id,
                "project": record.project,
                "started_at": started_at_iso,
                "ended_at": started_at_iso,
                "modalities": record.modality,
                "cost": record.cost_usd,
                "tenant_id": request_tenant_id,
                "agent_id": record.agent_id,
                "routed_llm": r_llm,
                "routed_tts": r_tts,
                "budget_ms": r_budget,
                "budget_overrun": r_overrun,
                "guardrails_active": guardrails_active,
                "guardrails_bypassed": guardrails_bypassed,
                "guardrail_snapshot_json": guardrail_snapshot_json,
            },
        )
    await session.commit()


async def log_audit_event(
    session: AsyncSession,
    entity_type: str,
    entity_id: str,
    action: str,
    changes: dict[str, Any] | None = None,
    source: str = "api",
) -> None:
    """Best-effort write of one config_audit_log row. Never raises."""
    try:
        await session.execute(
            text(
                "INSERT INTO config_audit_log "
                "(timestamp, entity_type, entity_id, action, changes_json, source) "
                "VALUES (:timestamp, :entity_type, :entity_id, :action, "
                " :changes_json, :source)"
            ),
            {
                "timestamp": time.time(),
                "entity_type": entity_type,
                "entity_id": entity_id,
                "action": action,
                "changes_json": json.dumps(changes) if changes else None,
                "source": source,
            },
        )
        await session.commit()
    except Exception:  # noqa: BLE001
        _logger.warning(
            "Failed to write audit log for %s/%s action=%s",
            entity_type,
            entity_id,
            action,
            exc_info=True,
        )


async def get_audit_log(
    session: AsyncSession,
    limit: int = 50,
    entity_type: str | None = None,
    entity_id: str | None = None,
    action: str | None = None,
) -> list[dict[str, Any]]:
    """Return recent config_audit_log entries."""
    conditions: list[str] = []
    params: dict[str, Any] = {"limit": limit}
    if entity_type:
        conditions.append("entity_type = :entity_type")
        params["entity_type"] = entity_type
    if entity_id:
        conditions.append("entity_id = :entity_id")
        params["entity_id"] = entity_id
    if action:
        conditions.append("action = :action")
        params["action"] = action
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = (
        "SELECT id, timestamp, entity_type, entity_id, action, "
        "changes_json, source FROM config_audit_log "
        f"{where} ORDER BY timestamp DESC LIMIT :limit"
    )

    result = await session.execute(text(query), params)
    rows: list[dict[str, Any]] = []
    for row in result:
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


_REQUEST_COLUMNS = (
    "id",
    "timestamp",
    "project",
    "modality",
    "model_id",
    "provider",
    "input_units",
    "output_units",
    "cached_input_units",
    "cost_usd",
    "pricing_source",
    "ttfb_ms",
    "total_latency_ms",
    "status",
    "fallback_from",
    "error_message",
    "metadata",
    "session_id",
    "tenant_id",
    "agent_id",
)


def _row_to_dict(row: Any) -> dict[str, Any]:
    record = {col: row[i] for i, col in enumerate(_REQUEST_COLUMNS)}
    if record.get("metadata"):
        try:
            record["metadata"] = json.loads(record["metadata"])
        except (ValueError, TypeError):
            pass
    return record


async def get_recent_requests(
    session: AsyncSession,
    limit: int = 100,
    modality: str | None = None,
    project: str | None = None,
    tenant: str | None = None,
) -> list[dict[str, Any]]:
    """Return the N newest request rows, optionally filtered."""
    conditions: list[str] = []
    params: dict[str, Any] = {"limit": limit}
    if modality:
        conditions.append("modality = :modality")
        params["modality"] = modality
    if project:
        conditions.append("project = :project")
        params["project"] = project
    if tenant is not None:
        if tenant == "":
            conditions.append("tenant_id IS NULL")
        else:
            conditions.append("tenant_id = :tenant")
            params["tenant"] = tenant
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    column_list = ", ".join(_REQUEST_COLUMNS)
    query = (
        f"SELECT {column_list} FROM requests {where} "
        "ORDER BY timestamp DESC LIMIT :limit"
    )

    result = await session.execute(text(query), params)
    return [_row_to_dict(row) for row in result]


async def get_requests_in_window(
    session: AsyncSession,
    start_ts: float | None = None,
    end_ts: float | None = None,
    project: str | None = None,
) -> list[dict[str, Any]]:
    """Return every request row falling in ``[start_ts, end_ts)``."""
    conditions: list[str] = []
    params: dict[str, Any] = {}
    if start_ts is not None:
        conditions.append("timestamp >= :start_ts")
        params["start_ts"] = start_ts
    if end_ts is not None:
        conditions.append("timestamp < :end_ts")
        params["end_ts"] = end_ts
    if project:
        conditions.append("project = :project")
        params["project"] = project
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    column_list = ", ".join(_REQUEST_COLUMNS)
    query = f"SELECT {column_list} FROM requests {where} ORDER BY timestamp ASC"
    result = await session.execute(text(query), params)
    return [_row_to_dict(row) for row in result]


__all__ = [
    "get_audit_log",
    "get_recent_requests",
    "get_requests_in_window",
    "log_audit_event",
    "log_request",
]
