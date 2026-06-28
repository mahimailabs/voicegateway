"""Dashboard endpoints under /api/sessions.

- GET /api/sessions: list recent sessions
- GET /api/sessions/{id}: single-session details
- GET /api/sessions/{id}/turns: per-turn rows
- GET /api/sessions/{id}/dead_air: dead-air events for one session

The replay endpoints on /api/sessions/{id}/replay live in
:mod:`voicegateway.server.api.dashboard.replay` to keep the replay-
related Pydantic shapes and the StaticFiles mount they share local.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from voicegateway.clickhouse import read_repository as ch_read
from voicegateway.repository import (
    dead_air_repository as dead_air,
)
from voicegateway.repository import (
    turns_repository as turns,
)
from voicegateway.server.api._deps import (
    Principal,
    get_gateway,
    require_principal,
    resolve_read_tenant,
)

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(prefix="/sessions", tags=["dashboard"])


@router.get("")
async def get_sessions(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    project: str | None = Query(None),
    tenant: str | None = Query(None),
    agent: str | None = Query(None),
    order_by: str = Query(
        "started_at_desc",
        pattern="^(started_at_desc|started_at_asc|cost_desc|cost_asc)$",
    ),
    gateway: Gateway = Depends(get_gateway),
    principal: Principal = Depends(require_principal),
) -> list[dict[str, Any]]:
    """Return recent voice sessions, scoped to the authenticated principal.

    Mirror of the /v1/sessions endpoint so the dashboard frontend can stay
    on a single origin in dev mode (the Vite proxy only forwards /api). The
    ``tenant`` query param is validated through :func:`resolve_read_tenant`:
    a non-admin asking for a foreign tenant is refused; omission scopes to
    the caller's own tenant. The raw param value is never trusted onward.
    """
    resolved = resolve_read_tenant(principal, tenant)
    if gateway.storage is None:
        return []
    ch_client = getattr(request.app.state, "ch_client", None)
    if ch_client is not None:
        if resolved is None:
            raise HTTPException(
                status_code=400,
                detail="specify a tenant; cross-tenant totals are at the "
                "admin endpoint",
            )
        return await ch_read.list_sessions(ch_client, tenant=resolved, limit=limit)
    return await gateway.storage.list_sessions(
        limit=limit, project=project, order_by=order_by, tenant=resolved, agent=agent
    )


@router.get("/{session_id}")
async def get_session_detail(
    session_id: str, gateway: Gateway = Depends(get_gateway)
) -> dict[str, Any]:
    """Return one session by id with per-modality breakdown.

    Mirror of /v1/sessions/{id}: returns the session row plus the
    ``by_modality`` and ``providers`` fields the storage method
    computes via a join on the requests table. 404 when no row
    matches.
    """
    if gateway.storage is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    row = await gateway.storage.get_session(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return row


@router.get("/{session_id}/turns")
async def get_session_turns(
    session_id: str, gateway: Gateway = Depends(get_gateway)
) -> dict[str, Any]:
    """Return ordered per-turn rows for a session (REQ-VG-METRICS-002).

    Used by the Metrics page's session-drill-down (T13). The shape
    mirrors the ``TurnRow`` dataclass; ``agent_speak_*`` and
    ``response_speed_ms`` are null when the agent never spoke for that
    turn (T02 contract).
    """
    if gateway.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        rows = await turns.list_turns_by_session(db, session_id)
    return {
        "session_id": session_id,
        "turns": [dataclasses.asdict(t) for t in rows],
    }


@router.get("/{session_id}/dead_air")
async def get_session_dead_air(
    session_id: str, gateway: Gateway = Depends(get_gateway)
) -> dict[str, Any]:
    """Return dead-air events for a session (REQ-VG-METRICS-004).

    Used by the Metrics page's DeadAirList drill-down (T14). Events
    are ordered by ``started_at_ms`` ASC, matching the chronological
    rendering the FE expects.
    """
    if gateway.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        events = await dead_air.list_events_by_session(db, session_id)
    return {
        "session_id": session_id,
        "events": [dataclasses.asdict(e) for e in events],
    }
