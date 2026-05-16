"""Dashboard endpoint: GET /api/routing/observations.

REQ-VG-ROUTE-003 (Routing observations view). Per-project provider-
latency snapshot powering the Routing dashboard view; the worker
rolls up every 15 minutes per OQ2 so freshness is at least hourly
per AC-3.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Query

from voicegateway.repository import (
    latency_observations_repository as latency_observations,
)
from voicegateway.server.api._deps import get_gateway

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(prefix="/routing", tags=["dashboard"])


@router.get("/observations")
async def get_routing_observations(
    project: str | None = Query(None),
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Per-project provider-latency snapshot powering the Routing dashboard view.

    Each entry carries the provider id, modality, p50/p95, sample_count,
    and the rolling window's start/end timestamps.

    Pass ``project`` to scope to one project; omit for every project's
    observations.
    """
    if gateway.storage is None:
        return {"observations": [], "filter": {"project": project}}
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        rows = (
            await latency_observations.get_for_project(db, project)
            if project
            else await latency_observations.read_all(db)
        )
    return {
        "observations": [dataclasses.asdict(r) for r in rows],
        "filter": {"project": project},
    }
