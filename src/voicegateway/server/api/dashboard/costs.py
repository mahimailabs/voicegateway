"""Dashboard endpoints: GET /api/costs, /api/latency.

These handlers share the read-only-aggregation shape (gateway +
storage required, period and project filters, tenant scoping) so they
sit in one router file. Distinct from ``server/api/costs.py``, which
serves the public ``/v1/costs`` endpoint with a different query schema
(start/end ISO dates, per_modality flag).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from voicegateway.clickhouse import read_repository as ch_read
from voicegateway.repository import turns_repository as turns_repo
from voicegateway.repository.cost_repository import resolve_window
from voicegateway.server.api._deps import (
    Principal,
    get_gateway,
    require_principal,
    resolve_read_tenant,
)

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(tags=["dashboard"])

# Message for the per-tenant ClickHouse endpoints when an admin asks for the
# all-tenants view: cross-tenant totals belong on the admin endpoint.
_NEEDS_TENANT = "specify a tenant; cross-tenant totals are at the admin endpoint"


@router.get("/costs")
async def get_costs(
    request: Request,
    period: str = Query("today", enum=["today", "week", "month", "all"]),
    project: str | None = Query(None),
    tenant: str | None = Query(None),
    agent: str | None = Query(None),
    revision: str | None = Query(
        None,
        description=(
            "Agent configuration revision. Pass an empty string to select rows "
            "that declared none."
        ),
    ),
    gateway: Gateway = Depends(get_gateway),
    principal: Principal = Depends(require_principal),
) -> dict:
    """Get cost summary for a period, scoped to the authenticated principal.

    Each ``by_model`` entry carries a ``pricing_source`` string (e.g.
    ``"voice-prices@0.0.8"`` for priced models or ``"voicegateway-local"``
    for self-hosted ones) so the dashboard can attribute each cost without
    a second round-trip. The ``tenant`` query param is validated through
    :func:`resolve_read_tenant`: a non-admin asking for a foreign tenant is
    refused; omission scopes to the caller's own tenant. The raw param value
    is never trusted onward.
    """
    resolved = resolve_read_tenant(principal, tenant)
    if gateway.storage is None:
        return {
            "period": period,
            "project": project,
            "total": 0.0,
            "by_provider": {},
            "by_model": {},
            "by_project": {},
        }
    ch_client = getattr(request.app.state, "ch_client", None)
    if ch_client is not None:
        if resolved is None:
            raise HTTPException(status_code=400, detail=_NEEDS_TENANT)
        since, until = resolve_window(period)
        summary = await ch_read.get_cost_summary(
            ch_client, tenant=resolved, since=since, until=until, project=project
        )
        summary["by_project"] = {}
        return summary
    summary = await gateway.storage.get_cost_summary(
        period,
        project=project,
        include_pricing_source=True,
        tenant=resolved,
        agent=agent,
        revision=revision,
    )
    # Always present, so a caller can see a rollout split without knowing to ask
    # for it. A p95 computed across a configuration change is a p95 of two
    # different agents, and this is what says so.
    summary["by_revision"] = await gateway.storage.get_cost_by_revision(
        period, project=project, tenant=resolved, agent=agent
    )
    if project is None:
        summary["by_project"] = await gateway.storage.get_cost_by_project(
            period, tenant=resolved, agent=agent
        )
    else:
        summary["by_project"] = {}
    return summary


@router.get("/costs/by-day")
async def get_costs_by_day(
    request: Request,
    period: str = Query("week", enum=["today", "week", "month", "all"]),
    project: str | None = Query(None),
    tenant: str | None = Query(None),
    agent: str | None = Query(None),
    gateway: Gateway = Depends(get_gateway),
    principal: Principal = Depends(require_principal),
) -> list[dict]:
    """Day-bucketed cost + request series for the Overview trend chart.

    Scoped to the authenticated principal through :func:`resolve_read_tenant`,
    exactly like ``/api/costs``: the raw ``tenant`` param is never trusted
    onward and a non-admin cannot read a foreign tenant's series.
    """
    resolved = resolve_read_tenant(principal, tenant)
    if gateway.storage is None:
        return []
    ch_client = getattr(request.app.state, "ch_client", None)
    if ch_client is not None:
        if resolved is None:
            raise HTTPException(status_code=400, detail=_NEEDS_TENANT)
        since, until = resolve_window(period)
        return await ch_read.get_cost_by_day(
            ch_client, tenant=resolved, since=since, until=until, project=project
        )
    return await gateway.storage.get_cost_by_day(
        period, project=project, tenant=resolved, agent=agent
    )


@router.get("/turns/response-speed")
async def get_turn_response_speed(
    request: Request,
    period: str = Query("week", enum=["today", "week", "month", "all"]),
    project: str | None = Query(None),
    tenant: str | None = Query(None),
    gateway: Gateway = Depends(get_gateway),
    principal: Principal = Depends(require_principal),
) -> dict:
    """p50/p95/p99 of ``response_speed_ms`` OVER TURNS, with the sample count.

    Deliberately not the same statistic as ``response_speed_ms`` on
    ``GET /api/metrics``, which is the MEAN OF PER-SESSION PERCENTILES. On two
    sessions with turn latencies [100,110,120,130,900] and [200,210,220] this
    returns 662 where that returns 482, and only one of them is a p95.

    It exists so a drift gate reading a remote collector pins the same
    mathematics a local one does. Without it, collector mode would have to read
    the per-session mean into a field named for a percentile, and a baseline
    pinned locally and checked remotely would compare two different statistics
    under one name while looking exactly like a comparison.
    """
    resolved = resolve_read_tenant(principal, tenant)
    if gateway.storage is None:
        return {"p50_ms": None, "p95_ms": None, "p99_ms": None, "samples": 0}
    since, until = resolve_window(period)
    # SECONDS -> MILLISECONDS. resolve_window speaks epoch seconds, because
    # `requests.timestamp` is seconds; turns are windowed on
    # `caller_speak_start_ms`, which is epoch MILLISECONDS. Passing the seconds
    # straight through would put every window in 1970 and return every turn ever
    # recorded, which reads as a healthy sample count rather than as an error.
    since_ms = int(since * 1000)
    until_ms = int(until * 1000) if until is not None else None
    async with gateway.storage._conn.session() as db:
        return await turns_repo.aggregate_response_speed(
            db,
            since_ms=since_ms,
            until_ms=until_ms,
            project=project,
            tenant=resolved,
        )


@router.get("/latency")
async def get_latency(
    request: Request,
    period: str = Query("today", enum=["today", "week"]),
    project: str | None = Query(None),
    tenant: str | None = Query(None),
    agent: str | None = Query(None),
    gateway: Gateway = Depends(get_gateway),
    principal: Principal = Depends(require_principal),
) -> dict:
    """Get latency statistics, scoped to the authenticated principal."""
    resolved = resolve_read_tenant(principal, tenant)
    if gateway.storage is None:
        return {}
    ch_client = getattr(request.app.state, "ch_client", None)
    if ch_client is not None:
        if resolved is None:
            raise HTTPException(status_code=400, detail=_NEEDS_TENANT)
        since, until = resolve_window(period)
        return await ch_read.get_latency_stats(
            ch_client, tenant=resolved, since=since, until=until, project=project
        )
    pcts = gateway.config.latency.get("percentiles") or [50.0, 95.0, 99.0]
    return await gateway.storage.get_latency_stats(
        period, project=project, percentiles=pcts, tenant=resolved, agent=agent
    )
