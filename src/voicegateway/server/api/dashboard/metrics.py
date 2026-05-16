"""Dashboard endpoint: GET /api/metrics.

The Foundry spelled the path as ``/v1/metrics`` but the dashboard's
convention is ``/api/*``. Keeping the metrics path under ``/api/*``
matches the frontend's expectation (the Metrics page hits this
endpoint directly); the main server may publish a parallel
``/v1/metrics`` in a later iteration if SDK consumers need it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from voicegateway.server.api._deps import get_gateway

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(tags=["dashboard"])


@router.get("/metrics")
async def get_metrics_summary(
    project: str | None = Query(None),
    days: int = Query(7, ge=1, le=365),
    tenant: str | None = Query(None),
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Aggregated voice-conversation metrics for the filter window.

    Filter:

    - ``project``: optional project name.
    - ``days``: trailing window from now (default 7, matches the Costs
      page default and Foundry Open Question 5's locked value).

    Aggregation is over the ``sessions`` rows in the window that carry
    measured v0.2.0 columns (REQ-VG-METRICS-006 graceful handling):
    pre-v0.2.0 sessions with NULL aggregates are counted in
    ``session_count`` but excluded from each metric average.

    The dead-air event count is filtered by session id (the events
    table's ``started_at_ms`` is monotonic-clock and cannot be
    correlated to wall-clock windows without a join through sessions).
    """
    if gateway.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")

    until = datetime.now(UTC)
    since = until - timedelta(days=days)
    since_iso = since.isoformat()
    until_iso = until.isoformat()

    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        where_clauses = ["started_at >= :since", "started_at < :until"]
        params: dict[str, Any] = {"since": since_iso, "until": until_iso}
        if project:
            where_clauses.append("project = :project")
            params["project"] = project
        if tenant is not None:
            if tenant == "":
                where_clauses.append("tenant_id IS NULL")
            else:
                where_clauses.append("tenant_id = :tenant")
                params["tenant"] = tenant
        where = " AND ".join(where_clauses)
        result = await db.execute(
            text(
                f"""SELECT id,
                           talk_time_seconds,
                           per_minute_cost_usd,
                           response_speed_p50_ms,
                           response_speed_p95_ms,
                           talk_over_rate
                      FROM sessions
                     WHERE {where}"""
            ),
            params,
        )
        rows = list(result)

        session_count = len(rows)
        measured_count = sum(1 for r in rows if r[2] is not None)

        def _avg_col(idx: int) -> float | None:
            vals = [float(r[idx]) for r in rows if r[idx] is not None]
            if not vals:
                return None
            return sum(vals) / len(vals)

        per_minute_cost_avg = _avg_col(2)
        response_speed_p50_avg = _avg_col(3)
        response_speed_p95_avg = _avg_col(4)
        talk_over_rate_avg = _avg_col(5)

        # Dead-air count joined through session_id.
        session_ids = [r[0] for r in rows]
        if session_ids:
            id_params = {f"sid_{i}": sid for i, sid in enumerate(session_ids)}
            placeholders = ",".join(f":{name}" for name in id_params)
            da_result = await db.execute(
                text(
                    f"SELECT COUNT(*) FROM dead_air_events "
                    f"WHERE session_id IN ({placeholders})"
                ),
                id_params,
            )
            da_row = da_result.first()
            dead_air_count = int(da_row[0]) if da_row else 0
        else:
            dead_air_count = 0

        return {
            "window": {
                "days": days,
                "since": since_iso,
                "until": until_iso,
            },
            "filter": {"project": project, "tenant": tenant},
            "session_count": session_count,
            "measured_session_count": measured_count,
            "per_minute_cost_usd_avg": per_minute_cost_avg,
            "response_speed_ms": {
                "p50": response_speed_p50_avg,
                "p95": response_speed_p95_avg,
            },
            "talk_over_rate": talk_over_rate_avg,
            "dead_air_event_count": dead_air_count,
        }
