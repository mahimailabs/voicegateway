"""Dashboard endpoints for the v0.3.0 conversation replay surface.

Four handlers backing the dashboard Replay page (T11) and the
per-project storage-usage view:

- GET    /api/sessions/{session_id}/replay
- DELETE /api/sessions/{session_id}/replay
- GET    /api/replay/storage
- POST   /api/projects/{project_id}/replay/retention

Same ``/api/*`` prefix as the v0.2.0 metrics endpoints; the main
server may publish ``/v1/*`` mirrors later if SDK consumers need them.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import text

from voicegateway.repository import (
    replay_repository as replay,
)
from voicegateway.server.api._deps import get_gateway

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(tags=["dashboard"])


@router.get("/sessions/{session_id}/replay")
async def get_session_replay(
    session_id: str, gateway: Gateway = Depends(get_gateway)
) -> dict[str, Any]:
    """Return the full time-ordered replay for one session.

    Used by the Replay page (T11) to pre-fetch the full timeline on
    page load (OQ3 resolution: pre-fetch over streaming). Each event
    carries its modality so the consumer routes to the right pane.

    Empty list when the session has no captured replay events
    (pre-v0.3.0 sessions or projects with ``replay.enabled: false``);
    the FE renders a PreV030Banner in that case (REQ-VG-REPLAY-001
    AC-3).
    """
    if gateway.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        events = await replay.read_full_replay(db, session_id)
    return {
        "session_id": session_id,
        "events": [dataclasses.asdict(e) for e in events],
    }


@router.delete("/sessions/{session_id}/replay")
async def delete_session_replay(
    session_id: str, gateway: Gateway = Depends(get_gateway)
) -> dict[str, Any]:
    """Delete every replay row tied to a session (REQ-VG-REPLAY-006 AC-3).

    Cascade across all four ``replay_*`` tables in one transaction.
    Returns the total row count deleted (across modalities) so the
    caller can confirm the cleanup landed.
    """
    if gateway.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        deleted = await replay.delete_replay(db, session_id)
    return {"session_id": session_id, "deleted_rows": deleted}


@router.get("/replay/storage")
async def get_replay_storage(
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Return per-project replay storage breakdown.

    Sums ``sessions.replay_size_bytes`` per project (the column is
    populated by T08's ``finalize_session_replay``). Sessions without
    captured replay (NULL replay_size_bytes) contribute 0 to the sum.
    The Refinery requires the dashboard surface this so "the cost is
    not invisible" to the developer.
    """
    if gateway.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        result = await db.execute(
            text(
                "SELECT project, COALESCE(SUM(replay_size_bytes), 0) "
                "FROM sessions "
                "WHERE replay_size_bytes IS NOT NULL "
                "GROUP BY project "
                "ORDER BY project ASC"
            )
        )
        per_project: list[dict[str, Any]] = []
        total = 0
        for row in result:
            project, size = row
            size_int = int(size or 0)
            per_project.append(
                {
                    "project": project,
                    "replay_size_bytes": size_int,
                }
            )
            total += size_int
        return {
            "total_replay_size_bytes": total,
            "by_project": per_project,
        }


@router.post("/projects/{project_id}/replay/retention")
async def update_replay_retention(
    project_id: str,
    body: dict[str, Any] = Body(...),
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Update the retention window for one project.

    Body shape: ``{"retention_days": N}`` (1..365 inclusive). The
    update is applied in-memory to ``gw.config.projects[project_id]
    .replay.retention_days`` so the retention worker (T06) picks it
    up on its next tick.

    v0.3.0 limitation: the update is not persisted to ``voicegw.yaml``
    on disk. Restart re-reads the original value. Persistence is a
    follow-up that needs config-file-write infrastructure; the in-
    memory mutation matches the runtime contract the retention worker
    reads from.
    """
    new_days_raw = body.get("retention_days")
    if not isinstance(new_days_raw, int) or new_days_raw < 1 or new_days_raw > 365:
        raise HTTPException(
            status_code=422,
            detail="retention_days must be an int in [1, 365]",
        )
    project = gateway.config.projects.get(project_id) if gateway.config else None
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    project.replay.retention_days = new_days_raw
    return {
        "project_id": project_id,
        "retention_days": new_days_raw,
    }
