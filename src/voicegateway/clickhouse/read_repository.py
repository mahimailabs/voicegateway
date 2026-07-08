"""Tenant-scoped ClickHouse read repository for dashboard rollup endpoints.

All public functions accept a clickhouse-connect AsyncClient (or compatible)
and return dict shapes that mirror the SQLite ORM repositories in
voicegateway/repository/ as closely as the ClickHouse schema allows.

Key design decisions:
- Every non-admin function requires ``tenant`` as a keyword argument with no
  default. This prevents accidental cross-tenant reads.
- Tenant scoping uses server-side bind parameters ({tenant:String}) so the
  value cannot be SQL-injected. Never use f-string interpolation for tenant.
- ``tenant_id`` is LowCardinality(String) with DEFAULT ''; empty string is
  the default-tenant sentinel (NOT NULL). The WHERE clause is always
  ``tenant_id = {tenant:String}`` (empty string matches default-tenant rows).
- ``requests`` is ReplacingMergeTree; duplicates collapse on merge.
  insert-time dedup tokens prevent landing duplicates, so SUM/COUNT without
  FINAL is acceptable for aggregate endpoints. For get_recent_requests (which
  is row-level and would show a transient pre-merge duplicate twice) we use
  LIMIT BY id to deduplicate at query time.
- ``sessions_agg`` uses SimpleAggregateFunction columns (sum, min, max,
  anyLast). No -Merge combiner is needed; plain sum/min/max/anyLast in SELECT
  is correct.
- ``timestamp`` is DateTime64(3,'UTC'). clickhouse-connect returns it as a
  Python datetime. We convert back to epoch-float-seconds via .timestamp() to
  match the existing dict shape that callers expect.
- ``since``/``until`` are epoch-float-seconds from callers. We bind them as
  floats and let ClickHouse coerce via fromUnixTimestamp (implicit cast when
  comparing DateTime64 to a numeric literal works; alternatively we pass an
  ISO string). We use explicit fromUnixTimestamp64Milli to be safe.
- ``get_cost_by_day`` buckets via toStartOfDay(timestamp,'UTC') and returns
  [{day: epoch_float, cost: float, requests: int}] ordered ascending by day.
  Epoch float is chosen for consistency with the timestamp field in
  get_recent_requests.
- ``get_cost_by_tenant_admin`` is the ONE cross-tenant read: no tenant filter,
  GROUP BY tenant_id. Task 7 must gate its endpoint behind require_scope("admin").
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# SQL constants
# ---------------------------------------------------------------------------

# Sentinel to represent "open-ended until": callers pass None for open-ended.
# We omit the upper bound clause entirely when until is None.

_SQL_COST_SUMMARY_TOTAL = """\
SELECT
    sum(cost_usd) AS total_cost
FROM telemetry.requests
WHERE tenant_id = {tenant:String}
  AND timestamp >= fromUnixTimestamp64Milli({since_ms:Int64})
  __UNTIL__
  __PROJECT__
"""

_SQL_COST_BY_PROVIDER = """\
SELECT
    provider,
    sum(cost_usd) AS cost,
    count()       AS request_count
FROM telemetry.requests
WHERE tenant_id = {tenant:String}
  AND timestamp >= fromUnixTimestamp64Milli({since_ms:Int64})
  __UNTIL__
  __PROJECT__
GROUP BY provider
ORDER BY cost DESC
"""

_SQL_COST_BY_MODEL = """\
SELECT
    model_id,
    sum(cost_usd) AS cost,
    count()       AS request_count
FROM telemetry.requests
WHERE tenant_id = {tenant:String}
  AND timestamp >= fromUnixTimestamp64Milli({since_ms:Int64})
  __UNTIL__
  __PROJECT__
GROUP BY model_id
ORDER BY cost DESC
"""

_SQL_COST_BY_PROJECT = """\
SELECT
    project,
    sum(cost_usd) AS cost,
    count()       AS request_count
FROM telemetry.requests
WHERE tenant_id = {tenant:String}
  AND timestamp >= fromUnixTimestamp64Milli({since_ms:Int64})
  __UNTIL__
  __PROJECT__
GROUP BY project
ORDER BY cost DESC
"""

_SQL_COST_BY_DAY = """\
SELECT
    toStartOfDay(timestamp, 'UTC') AS day,
    sum(cost_usd)                  AS cost,
    count()                        AS request_count
FROM telemetry.requests
WHERE tenant_id = {tenant:String}
  AND timestamp >= fromUnixTimestamp64Milli({since_ms:Int64})
  __UNTIL__
  __PROJECT__
GROUP BY day
ORDER BY day ASC
"""

_SQL_LATENCY_STATS = """\
SELECT
    model_id,
    avg(ttfb_ms)          AS avg_ttfb,
    avg(total_latency_ms) AS avg_latency,
    count()               AS request_count,
    quantilesTDigest(0.5, 0.95, 0.99)(ttfb_ms)          AS ttfb_pcts,
    quantilesTDigest(0.5, 0.95, 0.99)(total_latency_ms)  AS lat_pcts
FROM telemetry.requests
WHERE tenant_id = {tenant:String}
  AND timestamp >= fromUnixTimestamp64Milli({since_ms:Int64})
  __UNTIL__
  __PROJECT__
GROUP BY model_id
"""

# get_recent_requests: use LIMIT BY id to avoid showing transient pre-merge
# duplicates (ReplacingMergeTree collapses only on merge; at row-level a
# concurrent duplicate would appear twice without this guard).
_SQL_RECENT_REQUESTS = """\
SELECT
    id, timestamp, project, modality, model_id, provider,
    input_units, output_units, cached_input_units, cost_usd,
    pricing_source, ttfb_ms, total_latency_ms, status,
    fallback_from, error_message, metadata, session_id,
    tenant_id, agent_id
FROM telemetry.requests
WHERE tenant_id = {tenant:String}
  AND timestamp >= fromUnixTimestamp64Milli({since_ms:Int64})
  __UNTIL__
  __PROJECT__
ORDER BY timestamp DESC
LIMIT 1 BY id  -- keeps the newest row per id (ORDER BY timestamp DESC above)
LIMIT {limit:UInt32}
"""

# sessions_agg columns are SimpleAggregateFunction: use plain aggregates,
# no -Merge combiner needed.
_SQL_LIST_SESSIONS = """\
SELECT
    session_id,
    sum(request_count)  AS request_count,
    sum(total_cost_usd) AS total_cost_usd,
    min(started_at)     AS started_at,
    max(ended_at)       AS ended_at,
    anyLast(agent_id)   AS agent_id,
    tenant_id
FROM telemetry.sessions_agg
WHERE tenant_id = {tenant:String}
GROUP BY tenant_id, session_id
ORDER BY started_at DESC
LIMIT {limit:UInt32}
"""

# One call's individual requests, oldest first (the call timeline for the dashboard
# drill-down). Filters on the session_id column (exact + cheap); LIMIT 1 BY id dedups
# transient pre-merge duplicates like the recent-requests read.
_SQL_SESSION_REQUESTS = """\
SELECT
    id, timestamp, project, modality, model_id, provider,
    input_units, output_units, cached_input_units, cost_usd,
    pricing_source, ttfb_ms, total_latency_ms, status,
    fallback_from, error_message, metadata, session_id,
    tenant_id, agent_id
FROM telemetry.requests
WHERE tenant_id = {tenant:String}
  AND session_id = {session_id:String}
ORDER BY timestamp ASC
LIMIT 1 BY id
LIMIT {limit:UInt32}
"""

# Admin-only: no tenant filter. Returns all tenants.
_SQL_COST_BY_TENANT_ADMIN = """\
SELECT
    tenant_id,
    sum(cost_usd) AS cost,
    count()       AS request_count
FROM telemetry.requests
WHERE timestamp >= fromUnixTimestamp64Milli({since_ms:Int64})
  __UNTIL__
GROUP BY tenant_id
"""

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _since_ms(since: float) -> int:
    """Convert epoch-float-seconds to integer milliseconds for Int64 bind param."""
    return int(since * 1000)


def _until_clause(until: float | None) -> str:
    """Return the SQL fragment for the upper bound, or empty string."""
    if until is None:
        return ""
    return "AND timestamp < fromUnixTimestamp64Milli({until_ms:Int64})"


def _render(sql: str, *, until: float | None, project: str | None = None) -> str:
    """Replace __UNTIL__ and __PROJECT__ sentinel tokens in SQL templates.

    Using sentinel tokens (not str.format placeholders) avoids a KeyError when
    Python's str.format() tries to interpret the ClickHouse {name:Type} bind
    parameter syntax as format fields.
    """
    until_frag = _until_clause(until)
    project_frag = "AND project = {project:String}" if project else ""
    return sql.replace("__UNTIL__", until_frag).replace("__PROJECT__", project_frag)


def _params_base(
    *,
    tenant: str,
    since: float,
    until: float | None,
    project: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Build the common parameter dict for tenant-scoped queries."""
    params: dict[str, Any] = {
        "tenant": tenant,
        "since_ms": _since_ms(since),
    }
    if until is not None:
        params["until_ms"] = int(until * 1000)
    if project is not None:
        params["project"] = project
    if limit is not None:
        params["limit"] = limit
    return params


def _dt_to_epoch(val: Any) -> float:
    """Convert a datetime (from clickhouse-connect) to epoch float seconds."""
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=UTC)
        return float(val.timestamp())
    # Fallback: already numeric or string
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _pct_tuple_to_dict(pct_tuple: Any) -> dict[str, float | None]:
    """Map a 3-element quantilesTDigest result to {p50, p95, p99}."""
    keys = ("p50", "p95", "p99")
    if pct_tuple is None:
        return dict.fromkeys(keys)
    try:
        values = list(pct_tuple)
    except TypeError:
        return dict.fromkeys(keys)
    out: dict[str, float | None] = {}
    for i, key in enumerate(keys):
        try:
            raw = values[i]
            # NaN from ClickHouse when all samples are NULL
            if raw is None:
                out[key] = None
            else:
                v = float(raw)
                out[key] = None if math.isnan(v) else v
        except (IndexError, TypeError, ValueError):
            out[key] = None
    return out


def _parse_metadata(raw: Any) -> Any:
    """json.loads the metadata string; return as-is if not a string."""
    if isinstance(raw, str) and raw:
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return raw
    if isinstance(raw, dict):
        return raw
    return raw


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_cost_summary(
    client: Any,
    *,
    tenant: str,
    since: float,
    until: float | None,
    project: str | None = None,
) -> dict[str, Any]:
    """Return total/by_provider/by_model cost rollup for the window.

    Mirrors voicegateway.repository.cost_repository.get_cost_summary.
    'period' is set to a string describing the window; 'project' echoes the
    filter kwarg. This function requires tenant as an explicit keyword with no
    default so callers cannot accidentally omit it.
    """
    params = _params_base(tenant=tenant, since=since, until=until, project=project)

    total_sql = _render(_SQL_COST_SUMMARY_TOTAL, until=until, project=project)
    total_result = await client.query(total_sql, parameters=params)
    total = 0.0
    if total_result.result_rows:
        raw_total = total_result.result_rows[0][0]
        total = float(raw_total) if raw_total is not None else 0.0

    prov_sql = _render(_SQL_COST_BY_PROVIDER, until=until, project=project)
    prov_result = await client.query(prov_sql, parameters=params)
    by_provider: dict[str, dict[str, Any]] = {
        row[0]: {"cost": float(row[1] or 0.0), "requests": int(row[2] or 0)}
        for row in prov_result.result_rows
    }

    model_sql = _render(_SQL_COST_BY_MODEL, until=until, project=project)
    model_result = await client.query(model_sql, parameters=params)
    by_model: dict[str, dict[str, Any]] = {
        row[0]: {"cost": float(row[1] or 0.0), "requests": int(row[2] or 0)}
        for row in model_result.result_rows
    }

    proj_sql = _render(_SQL_COST_BY_PROJECT, until=until, project=project)
    proj_result = await client.query(proj_sql, parameters=params)
    by_project: dict[str, dict[str, Any]] = {
        row[0]: {"cost": float(row[1] or 0.0), "requests": int(row[2] or 0)}
        for row in proj_result.result_rows
    }

    period_label = f"{since}..{until if until is not None else 'now'}"
    return {
        "period": period_label,
        "project": project,
        "total": total,
        "by_provider": by_provider,
        "by_model": by_model,
        "by_project": by_project,
    }


async def get_cost_by_day(
    client: Any,
    *,
    tenant: str,
    since: float,
    until: float | None,
    project: str | None = None,
) -> list[dict[str, Any]]:
    """Return day-bucketed cost series for the window, ordered ascending.

    Each entry: {"day": epoch_float_seconds, "cost": float, "requests": int}.
    Day is the UTC start-of-day as epoch float, consistent with the timestamp
    field convention used throughout (epoch-float-seconds).
    """
    params = _params_base(tenant=tenant, since=since, until=until, project=project)
    sql = _render(_SQL_COST_BY_DAY, until=until, project=project)
    result = await client.query(sql, parameters=params)
    rows = []
    for row in result.result_rows:
        day_epoch = _dt_to_epoch(row[0])
        rows.append(
            {
                "day": day_epoch,
                "cost": float(row[1] or 0.0),
                "requests": int(row[2] or 0),
            }
        )
    return rows


async def get_latency_stats(
    client: Any,
    *,
    tenant: str,
    since: float,
    until: float | None,
    project: str | None = None,
) -> dict[str, Any]:
    """Return per-model latency rollup with avg + p50/p95/p99 percentiles.

    Mirrors voicegateway.repository.latency_repository.get_latency_stats.
    Uses ClickHouse quantilesTDigest(0.5,0.95,0.99)(col) to compute server-side
    percentiles in a single pass (no Python-side sample collection needed).
    A model with no non-null latency samples gets None for each percentile,
    matching compute_percentiles([], ...) behavior.
    """
    params = _params_base(tenant=tenant, since=since, until=until, project=project)
    sql = _render(_SQL_LATENCY_STATS, until=until, project=project)
    result = await client.query(sql, parameters=params)

    stats: dict[str, dict[str, Any]] = {}
    for row in result.result_rows:
        model_id = row[0]
        avg_ttfb = float(row[1]) if row[1] is not None else None
        avg_latency = float(row[2]) if row[2] is not None else None
        request_count = int(row[3] or 0)
        ttfb_pcts = _pct_tuple_to_dict(row[4])
        lat_pcts = _pct_tuple_to_dict(row[5])
        stats[model_id] = {
            "avg_ttfb_ms": avg_ttfb,
            "avg_latency_ms": avg_latency,
            "request_count": request_count,
            "ttfb_percentiles": ttfb_pcts,
            "latency_percentiles": lat_pcts,
        }
    return stats


async def get_recent_requests(
    client: Any,
    *,
    tenant: str,
    since: float,
    until: float | None,
    limit: int = 100,
    project: str | None = None,
) -> list[dict[str, Any]]:
    """Return the N newest request rows for the tenant, ordered newest first.

    Mirrors voicegateway.repository.request_log_repository.get_recent_requests.
    Uses LIMIT BY id to guard against transient pre-merge duplicates from
    ReplacingMergeTree (rows collapse only on background merge; a duplicate
    inserted within the same buffer window would appear twice without this).
    timestamp is converted to epoch-float-seconds; metadata is json.loads-ed.
    """
    params = _params_base(
        tenant=tenant, since=since, until=until, project=project, limit=limit
    )
    sql = _render(_SQL_RECENT_REQUESTS, until=until, project=project)
    result = await client.query(sql, parameters=params)

    _COLS = (
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

    rows = []
    for row in result.result_rows:
        record: dict[str, Any] = {col: row[i] for i, col in enumerate(_COLS)}
        record["timestamp"] = _dt_to_epoch(record["timestamp"])
        record["metadata"] = _parse_metadata(record["metadata"])
        rows.append(record)
    return rows


async def get_session_requests(
    client: Any,
    *,
    tenant: str,
    session_id: str,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Return every request row for one session, oldest first (the call's timeline).

    Filters on the ``session_id`` column (exact and cheap), so it powers the dashboard call
    drill-down: the STT/LLM/TTS sequence with per-request latency, cost, and status, plus each
    row's ``metadata`` (from which the phone/web channel is read). Bounded by ``limit`` so one
    pathological session cannot return unbounded rows.
    """
    params = {"tenant": tenant, "session_id": session_id, "limit": limit}
    result = await client.query(_SQL_SESSION_REQUESTS, parameters=params)

    _COLS = (
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

    rows = []
    for row in result.result_rows:
        record: dict[str, Any] = {col: row[i] for i, col in enumerate(_COLS)}
        record["timestamp"] = _dt_to_epoch(record["timestamp"])
        record["metadata"] = _parse_metadata(record["metadata"])
        rows.append(record)
    return rows


async def list_sessions(
    client: Any,
    *,
    tenant: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return recent sessions from sessions_agg for the tenant.

    Mirrors voicegateway.repository.session_repository.list_sessions for the
    subset of columns available in the ClickHouse MV (sessions_agg only carries
    tenant_id, session_id, request_count, total_cost_usd, started_at, ended_at,
    agent_id -- no project, modalities, or routed_* columns).
    Ordered by started_at DESC.
    """
    params: dict[str, Any] = {"tenant": tenant, "limit": limit}
    result = await client.query(_SQL_LIST_SESSIONS, parameters=params)

    sessions = []
    for row in result.result_rows:
        # Columns: session_id, request_count, total_cost_usd, started_at, ended_at,
        #          agent_id, tenant_id
        sessions.append(
            {
                "id": row[0],
                "request_count": int(row[1] or 0),
                "total_cost_usd": float(row[2] or 0.0),
                "started_at": _dt_to_epoch(row[3]),
                "ended_at": _dt_to_epoch(row[4]),
                "agent_id": row[5] if row[5] is not None else "",
                "tenant_id": row[6] if row[6] is not None else "",
            }
        )
    return sessions


async def get_cost_by_tenant_admin(
    client: Any,
    *,
    since: float,
    until: float | None,
) -> dict[str, dict[str, Any]]:
    """Return cost rollup grouped by tenant_id -- the ONE cross-tenant read.

    This function has NO tenant filter. Task 7 must gate the endpoint that
    calls this behind require_scope("admin"). Returns:
    {tenant_id: {"cost": float, "requests": int}}
    """
    params: dict[str, Any] = {"since_ms": _since_ms(since)}
    if until is not None:
        params["until_ms"] = int(until * 1000)
    sql = _SQL_COST_BY_TENANT_ADMIN.replace("__UNTIL__", _until_clause(until))
    result = await client.query(sql, parameters=params)
    return {
        row[0]: {"cost": float(row[1] or 0.0), "requests": int(row[2] or 0)}
        for row in result.result_rows
    }


__all__ = [
    "get_cost_by_day",
    "get_cost_by_tenant_admin",
    "get_cost_summary",
    "get_latency_stats",
    "get_recent_requests",
    "list_sessions",
]
