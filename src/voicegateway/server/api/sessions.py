"""Session endpoints: GET /v1/sessions, GET /v1/sessions/{id}."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query

from voicegateway.server.api._deps import get_gateway

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("")
async def list_sessions(
    limit: int = Query(100, ge=1, le=1000),
    project: str | None = Query(None),
    order_by: str = Query(
        "started_at_desc",
        pattern="^(started_at_desc|started_at_asc|cost_desc|cost_asc)$",
    ),
    gateway: Gateway = Depends(get_gateway),
) -> list[dict]:
    """Return recent voice sessions, ordered per ``order_by``."""
    if gateway.storage is None:
        return []
    return await gateway.storage.list_sessions(
        limit=limit, project=project, order_by=order_by
    )


@router.get("/{session_id}")
async def session_detail(
    session_id: str,
    gateway: Gateway = Depends(get_gateway),
) -> dict:
    """Return one session by id with a per-modality cost breakdown."""
    if gateway.storage is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    row = await gateway.storage.get_session(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return row
