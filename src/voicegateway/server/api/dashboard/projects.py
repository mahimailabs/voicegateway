"""Dashboard endpoints: GET /api/projects."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends

from voicegateway.server.api._deps import get_gateway

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(prefix="/projects", tags=["dashboard"])


@router.get("")
async def get_projects(gateway: Gateway = Depends(get_gateway)) -> dict:
    """List configured projects with today's stats."""
    projects = gateway.list_projects()
    stats: dict[str, Any] = {}
    if gateway.storage is not None:
        for p in projects:
            stats[p["id"]] = await gateway.storage.get_project_stats(p["id"])
    return {"projects": projects, "stats": stats}


