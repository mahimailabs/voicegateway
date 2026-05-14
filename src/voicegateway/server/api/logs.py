"""Recent-request log endpoint: GET /v1/logs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query

from voicegateway.server.api._deps import get_gateway

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("")
async def list_logs(
    limit: int = Query(100, ge=1, le=1000),
    modality: str | None = Query(None),
    project: str | None = Query(None),
    gateway: Gateway = Depends(get_gateway),
) -> list[dict]:
    if gateway.storage is None:
        return []
    return await gateway.storage.get_recent_requests(
        limit=limit, modality=modality, project=project
    )
