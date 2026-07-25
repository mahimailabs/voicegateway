"""Async repo for request log writes + audit events + raw row reads (ORM).

The sessions UPSERT stays as a ``text()`` clause: it carries the
INSTR-based modality CSV union, the started_at/ended_at min/max
preservation, and the COALESCE-vs-null preservation for the routing
aggregate columns. These semantics are the algorithm; converting to ORM
``on_conflict_do_update`` with chained CASE / INSTR expressions would
only obscure the intent.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from voicegateway.inference.session.context import (
    current_routing_decision,
    current_tenant,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from voicegateway.models.request_model import RequestRecord

_logger = logging.getLogger(__name__)


_INSERT_REQUEST = text(
    """INSERT INTO requests
       (id, timestamp, project, modality, model_id, provider,
        input_units, output_units, cached_input_units, cost_usd, pricing_source,
        rated_price_usd, rate_rule,
        ttfb_ms, total_latency_ms, status,
        fallback_from, error_message, metadata, session_id,
        tenant_id, agent_id)
       VALUES (:id, :timestamp, :project, :modality, :model_id, :provider,
               :input_units, :output_units, :cached_input_units, :cost_usd, :pricing_source,
               :rated_price_usd, :rate_rule,
               :ttfb_ms, :total_latency_ms, :status,
               :fallback_from, :error_message, :metadata, :session_id,
               :tenant_id, :agent_id)"""
)


# Sessions UPSERT — preserved byte-for-byte from the legacy aiosqlite form.
# Carries the INSTR-based modality CSV union, the started_at/ended_at min/max
# preservation, and the COALESCE-vs-null preservation on the routing
# aggregate columns. Translating to on_conflict_do_update would
# obscure these invariants.
_UPSERT_SESSION = text(
    """INSERT INTO sessions
       (id, project, started_at, ended_at, modalities,
        total_cost_usd, request_count, tenant_id, agent_id,
        routed_llm, routed_tts, budget_ms, budget_overrun)
       VALUES (:id, :project, :started_at, :ended_at, :modalities,
               :cost, 1, :tenant_id, :agent_id,
               :routed_llm, :routed_tts, :budget_ms, :budget_overrun)
       ON CONFLICT(id) DO UPDATE SET
           total_cost_usd = sessions.total_cost_usd + excluded.total_cost_usd,
           request_count = sessions.request_count + 1,
           tenant_id = COALESCE(sessions.tenant_id, excluded.tenant_id),
           agent_id = COALESCE(sessions.agent_id, excluded.agent_id),
           routed_llm = COALESCE(sessions.routed_llm, excluded.routed_llm),
           routed_tts = COALESCE(sessions.routed_tts, excluded.routed_tts),
           budget_ms = COALESCE(sessions.budget_ms, excluded.budget_ms),
           budget_overrun = COALESCE(
               sessions.budget_overrun, excluded.budget_overrun
           ),
           started_at = CASE
               WHEN sessions.started_at IS NULL THEN excluded.started_at
               WHEN sessions.started_at > excluded.started_at
                   THEN excluded.started_at
               ELSE sessions.started_at
           END,
           ended_at = CASE
               WHEN sessions.ended_at IS NULL THEN excluded.ended_at
               WHEN sessions.ended_at < excluded.ended_at THEN excluded.ended_at
               ELSE sessions.ended_at
           END,
           modalities = CASE
               WHEN sessions.modalities = '' THEN excluded.modalities
               WHEN INSTR(
                   ',' || sessions.modalities || ',',
                   ',' || excluded.modalities || ','
               ) > 0 THEN sessions.modalities
               ELSE sessions.modalities || ',' || excluded.modalities
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
            "rated_price_usd": record.rated_price_usd,
            "rate_rule": record.rate_rule,
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
    "rated_price_usd",
    "rate_rule",
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


# Rooms a VoiceGateway probe creates are named ``vg-probe-<agent>-<hex8>``
# (livekit_diag.latency.ProbeRunner.probe). That prefix is what makes a probe's
# rows self-identifying: they are ordinary ``requests`` rows, so nothing else
# distinguishes synthetic probe traffic from a real call. Matching on the room
# name (rather than a new column) means the exclusion also works for agents that
# are already deployed, with no migration and no agent-side change.
PROBE_ROOM_PREFIX = "vg-probe-"

# ``"room": "vg-probe-`` exactly as it appears in the serialized metadata JSON.
# Built with json.dumps so it matches the write path byte for byte, then sliced
# to drop the closing quote so it matches any room STARTING with the prefix.
_PROBE_ROOM_NEEDLE = "%" + json.dumps({"room": PROBE_ROOM_PREFIX})[1:-2] + "%"

# A row with NULL metadata is real traffic that simply carried no context, so it
# must survive the filter: ``NULL NOT LIKE x`` is NULL (i.e. dropped) in SQL, so
# the IS NULL arm is load-bearing, not defensive noise.
_EXCLUDE_PROBES_SQL = "(metadata IS NULL OR metadata NOT LIKE :probe_needle)"

# The metadata key ``attach`` stamps with the observed LiveKit Job.agent_name.
_DISPATCH_NAME_NEEDLE = "%" + json.dumps("dispatch_name") + ":%"


def exclude_probes_clause() -> tuple[str, dict[str, Any]]:
    """Return the SQL predicate + bind params that drop VoiceGateway probe rows.

    Handed out as a pair so every caller that reports on *real traffic* filters
    identically, and so the needle is built in exactly one place. ``AND`` the
    predicate into a WHERE clause and merge the params.
    """
    return _EXCLUDE_PROBES_SQL, {"probe_needle": _PROBE_ROOM_NEEDLE}


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
    agent: str | None = None,
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
    if agent is not None:
        if agent == "":
            conditions.append("agent_id IS NULL")
        else:
            conditions.append("agent_id = :agent")
            params["agent"] = agent
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    column_list = ", ".join(_REQUEST_COLUMNS)
    query = (
        f"SELECT {column_list} FROM requests {where} "
        "ORDER BY timestamp DESC LIMIT :limit"
    )

    result = await session.execute(text(query), params)
    return [_row_to_dict(row) for row in result]


async def get_requests_for_room(
    session: AsyncSession,
    room: str,
    since: float | None = None,
) -> list[dict[str, Any]]:
    """Return every request row tagged ``metadata.room == room``.

    Used by ``voicegw livekit latency`` to correlate a throwaway probe room
    with the STT/LLM/TTS + turn-detection rows the instrumented agent wrote
    (``attach`` stamps the room on ``metadata``). There is no room column, so
    a ``LIKE`` on the serialized ``metadata`` JSON pre-filters, then an exact
    parsed match rejects any incidental substring hit. The ``LIKE`` only ever
    widens the candidate set (``_`` / ``%`` in a room name match more, never
    fewer), so the exact match below never drops a true row.

    ``since`` bounds the scan to ``timestamp >= since`` (epoch seconds). The
    metadata ``LIKE`` is a non-sargable full scan, so a time bound lets the
    ``idx_requests_timestamp`` index cut the rows read: use it when rolling up a
    long-lived room over a window (the Server-page cost annotation). ``None``
    keeps the original unbounded behavior for the ephemeral-probe caller.
    """
    column_list = ", ".join(_REQUEST_COLUMNS)
    # Build the needle with json.dumps so it matches the write path byte-for-byte,
    # including ensure_ascii escaping (\uXXXX for non-ASCII, escaped quotes): a
    # raw f-string would miss a room name with such characters. ``[1:-1]`` strips
    # the object braces, leaving the exact ``"room": "<escaped>"`` substring.
    key = json.dumps({"room": room})[1:-1]
    like = f"%{key}%"
    params: dict[str, Any] = {"like": like}
    where = "metadata LIKE :like"
    if since is not None:
        where += " AND timestamp >= :since"
        params["since"] = since
    query = f"SELECT {column_list} FROM requests WHERE {where} ORDER BY timestamp ASC"
    result = await session.execute(text(query), params)
    rows = [_row_to_dict(row) for row in result]
    return [
        r
        for r in rows
        if isinstance(r.get("metadata"), dict) and r["metadata"].get("room") == room
    ]


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


async def read_last_seen_models(
    session: AsyncSession, agent_ids: list[str] | None = None
) -> dict[str, dict[str, str]]:
    """Last-seen ``provider/model`` per (agent_id, modality). Returns
    {agent_id: {modality: "provider/model"}}. Newest timestamp wins per group."""
    where = "agent_id IS NOT NULL"
    params: dict[str, Any] = {}
    if agent_ids:
        keys = ", ".join(f":a{i}" for i in range(len(agent_ids)))
        where += f" AND agent_id IN ({keys})"
        params = {f"a{i}": a for i, a in enumerate(agent_ids)}
    # One row per (agent_id, modality): the model_id/provider at MAX(timestamp).
    sql = text(f"""
        SELECT r.agent_id, r.modality, r.provider, r.model_id
        FROM requests r
        JOIN (
            SELECT agent_id, modality, MAX(timestamp) AS mt
            FROM requests
            WHERE {where}
            GROUP BY agent_id, modality
        ) latest
        ON r.agent_id = latest.agent_id
        AND r.modality = latest.modality
        AND r.timestamp = latest.mt
    """)
    result = await session.execute(sql, params)
    out: dict[str, dict[str, str]] = {}
    for row in result.mappings():
        model = row["model_id"]
        if row["provider"] and "/" not in model:
            model = f"{row['provider']}/{model}"
        out.setdefault(row["agent_id"], {})[row["modality"]] = model
    return out


async def read_avg_ttfb_by_modality(
    session: AsyncSession,
    agent_ids: list[str] | None = None,
    *,
    since: float | None = None,
) -> dict[str, dict[str, float]]:
    """Average first-byte latency (``ttfb_ms``) per (agent_id, modality).

    Returns ``{agent_id: {modality: avg_ttfb_ms}}``. Feeds the Overview agent
    cards' STT/LLM/TTS latency waterfall. Rows with a NULL ttfb_ms drop out of
    the AVG. Scoped to ``since`` (epoch seconds) when given, so the index can
    match its 24h window.

    Rows from VoiceGateway's own probes are excluded: the waterfall describes
    how this agent serves real callers, and a synthetic probe (a fixed 2-second
    utterance, no barge-in, a cold room) is not that. The probe's own numbers are
    shown separately, as one sample, by the per-agent probe endpoint.
    """
    where = f"agent_id IS NOT NULL AND ttfb_ms IS NOT NULL AND {_EXCLUDE_PROBES_SQL}"
    params: dict[str, Any] = {"probe_needle": _PROBE_ROOM_NEEDLE}
    if agent_ids:
        keys = ", ".join(f":a{i}" for i in range(len(agent_ids)))
        where += f" AND agent_id IN ({keys})"
        params.update({f"a{i}": a for i, a in enumerate(agent_ids)})
    if since is not None:
        where += " AND timestamp >= :since"
        params["since"] = since
    sql = text(f"""
        SELECT agent_id, modality, AVG(ttfb_ms) AS avg_ttfb
        FROM requests
        WHERE {where}
        GROUP BY agent_id, modality
    """)
    result = await session.execute(sql, params)
    out: dict[str, dict[str, float]] = {}
    for row in result.mappings():
        out.setdefault(row["agent_id"], {})[row["modality"]] = float(row["avg_ttfb"])
    return out


async def read_last_seen_dispatch_name(
    session: AsyncSession,
    agent_ids: list[str] | None = None,
    *,
    since: float | None = None,
) -> dict[str, str]:
    """Last-observed LiveKit dispatch name per agent_id.

    Returns ``{agent_id: dispatch_name}``, where the value is ``Job.agent_name``
    as ``attach`` saw it on the agent's most recent instrumented call. An empty
    string is a real, distinct answer (the worker registered no agent_name, so it
    is on automatic dispatch); an agent absent from the mapping was simply never
    observed, and the dashboard must not guess a name for it.

    This is the only honest source for "can the dashboard dispatch a probe to
    this agent". Dispatch eligibility is decided at worker registration inside
    the agent's own process, so it can be read back from a job it actually ran,
    never invented here. Agents whose telemetry goes to a remote collector write
    no local rows and so never appear.
    """
    params: dict[str, Any] = {"dispatch_needle": _DISPATCH_NAME_NEEDLE}
    if agent_ids:
        params.update({f"a{i}": a for i, a in enumerate(agent_ids)})
    if since is not None:
        params["since"] = since

    def _predicate(prefix: str) -> str:
        # The same filter is applied twice, once inside the aggregate and once
        # on the joined rows, so it is generated rather than reused as a string:
        # the outer copy sits in a two-table scope where a bare ``agent_id``
        # is ambiguous and SQLite rejects the whole query.
        parts = [
            f"{prefix}agent_id IS NOT NULL",
            f"{prefix}metadata LIKE :dispatch_needle",
        ]
        if agent_ids:
            keys = ", ".join(f":a{i}" for i in range(len(agent_ids)))
            parts.append(f"{prefix}agent_id IN ({keys})")
        if since is not None:
            parts.append(f"{prefix}timestamp >= :since")
        return " AND ".join(parts)

    # Newest qualifying row per agent, same JOIN-on-MAX shape as
    # read_last_seen_models so one chatty agent cannot crowd out a quiet one.
    # The outer filter matters: an agent can write several rows at the same
    # timestamp (an eou row and an llm row from one turn), and only some of them
    # carry a dispatch_name, so the join alone would hand back rows that cannot
    # answer the question.
    sql = text(f"""
        SELECT r.agent_id, r.metadata
        FROM requests r
        JOIN (
            SELECT agent_id, MAX(timestamp) AS mt
            FROM requests
            WHERE {_predicate("")}
            GROUP BY agent_id
        ) latest
        ON r.agent_id = latest.agent_id AND r.timestamp = latest.mt
        WHERE {_predicate("r.")}
    """)
    result = await session.execute(sql, params)
    out: dict[str, str] = {}
    for row in result.mappings():
        try:
            meta = json.loads(row["metadata"]) if row["metadata"] else None
        except (ValueError, TypeError):
            continue
        if not isinstance(meta, dict):
            continue
        name = meta.get("dispatch_name")
        # The LIKE only pre-filters the serialized JSON; this is the exact match
        # that rejects an incidental substring hit.
        if isinstance(name, str):
            out.setdefault(row["agent_id"], name)
    return out


async def read_models_in_use(session: AsyncSession) -> list[dict[str, str]]:
    """Distinct (modality, provider, model_id) seen in requests, newest first."""
    sql = text("""
        SELECT modality, provider, model_id, MAX(timestamp) AS last_seen
        FROM requests
        GROUP BY modality, provider, model_id
        ORDER BY last_seen DESC
    """)
    result = await session.execute(sql)
    rows = []
    for r in result.mappings():
        model = r["model_id"]
        if r["provider"] and "/" not in model:
            model = f"{r['provider']}/{model}"
        rows.append(
            {
                "modality": r["modality"],
                "provider": r["provider"] or "",
                "model": model,
            }
        )
    return rows


__all__ = [
    "get_audit_log",
    "get_recent_requests",
    "get_requests_for_room",
    "get_requests_in_window",
    "log_audit_event",
    "log_request",
    "PROBE_ROOM_PREFIX",
    "read_avg_ttfb_by_modality",
    "read_last_seen_dispatch_name",
    "read_last_seen_models",
    "read_models_in_use",
]
