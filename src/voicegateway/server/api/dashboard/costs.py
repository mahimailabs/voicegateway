"""Dashboard endpoints: GET /api/costs, /api/latency, /api/logs.

These three handlers share the read-only-aggregation shape (gateway +
storage required, period and project filters, tenant scoping) so they
sit in one router file. Distinct from ``server/api/costs.py``, which
serves the public ``/v1/costs`` endpoint with a different query schema
(start/end ISO dates, per_modality flag).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query

from voicegateway.server.api._deps import get_gateway

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(tags=["dashboard"])


@router.get("/costs")
async def get_costs(
    period: str = Query("today", enum=["today", "week", "month", "all"]),
    project: str | None = Query(None),
    tenant: str | None = Query(None),
    agent: str | None = Query(None),
    gateway: Gateway = Depends(get_gateway),
) -> dict:
    """Get cost summary for a period, optionally filtered by project and tenant.

    Each ``by_model`` entry carries a ``pricing_source`` string (e.g.
    ``"voice-prices@0.0.8"`` for priced models or ``"voicegateway-local"``
    for self-hosted ones) so the dashboard can attribute each cost without
    a second round-trip. ``tenant`` accepts a tenant id; pass the empty
    string to scope to the unattributed bucket (REQ-VG-TENANT-002).
    """
    if gateway.storage is None:
        return {
            "period": period,
            "project": project,
            "total": 0.0,
            "by_provider": {},
            "by_model": {},
            "by_project": {},
        }
    summary = await gateway.storage.get_cost_summary(
        period, project=project, include_pricing_source=True, tenant=tenant, agent=agent
    )
    if project is None:
        summary["by_project"] = await gateway.storage.get_cost_by_project(
            period, tenant=tenant, agent=agent
        )
    else:
        summary["by_project"] = {}
    return summary


@router.get("/latency")
async def get_latency(
    period: str = Query("today", enum=["today", "week"]),
    project: str | None = Query(None),
    tenant: str | None = Query(None),
    agent: str | None = Query(None),
    gateway: Gateway = Depends(get_gateway),
) -> dict:
    """Get latency statistics, optionally filtered by project and tenant."""
    if gateway.storage is None:
        return {}
    pcts = gateway.config.latency.get("percentiles") or [50.0, 95.0, 99.0]
    return await gateway.storage.get_latency_stats(
        period, project=project, percentiles=pcts, tenant=tenant, agent=agent
    )


@router.get("/logs")
async def get_logs(
    limit: int = Query(100, ge=1, le=1000),
    modality: str | None = Query(None, enum=["stt", "llm", "tts"]),
    project: str | None = Query(None),
    tenant: str | None = Query(None),
    agent: str | None = Query(None),
    gateway: Gateway = Depends(get_gateway),
) -> list[dict]:
    """Get recent request logs, filtered by modality, project, tenant, agent."""
    if gateway.storage is None:
        return []
    return await gateway.storage.get_recent_requests(
        limit=limit, modality=modality, project=project, tenant=tenant, agent=agent
    )
