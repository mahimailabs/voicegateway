"""Dashboard endpoints for the v0.3.0 conversation replay surface.

Four handlers backing the dashboard Replay page (T11) and the
per-project storage-usage view:

- GET    /api/sessions/{session_id}/replay
- DELETE /api/sessions/{session_id}/replay
- GET    /api/replay/storage
- POST   /api/projects/{project_id}/replay/retention

Same ``/api/*`` prefix as the v0.2.0 metrics endpoints; the main
server may publish ``/v1/*`` mirrors later if SDK consumers need them.

Auth here is declared per handler, not on the router, because the four
handlers do not want the same gate:

- the two READS take :func:`require_principal`, the dependency the sibling
  dashboard reads use, plus a tenant check. ``GET /sessions/{id}/replay``
  is the fifth read under a session id, so it takes exactly the dependency
  and the check the other four take in
  :mod:`voicegateway.server.api.dashboard.sessions`; ``GET /replay/storage``
  aggregates every session row on the deployment, so it scopes its own
  query.
- the DELETE and the retention POST take ``require_scope(ADMIN_SCOPE)``,
  which is what every write on a dashboard router takes (the API-key
  routers, the diagnostics run, the agent probe, the branding logo
  upload). One destroys captured payloads outright and the other sets how
  long any of them survive.

A router-level dependency would have to be the strictest of those, and
putting the admin scope on the replay reads would refuse a read-scoped
operator on the Replay page. Every one of these gates is a no-op while no
API keys are configured, so the self-hosted default is unchanged.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import text

from voicegateway.core.auth import ADMIN_SCOPE
from voicegateway.repository import (
    replay_repository as replay,
)
from voicegateway.server.api._deps import (
    Principal,
    get_gateway,
    require_principal,
    require_scope,
    resolve_read_tenant,
)
from voicegateway.server.api.dashboard.sessions import require_visible_session

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(tags=["dashboard"])


@router.get("/sessions/{session_id}/replay")
async def get_session_replay(
    session_id: str,
    gateway: Gateway = Depends(get_gateway),
    principal: Principal = Depends(require_principal),
) -> dict[str, Any]:
    """Return the full time-ordered replay for one session.

    Used by the Replay page (T11) to pre-fetch the full timeline on
    page load (OQ3 resolution: pre-fetch over streaming). Each event
    carries its modality so the consumer routes to the right pane.

    Empty list when the session has no captured replay events
    (pre-v0.3.0 sessions or projects with ``replay.enabled: false``);
    the FE renders a PreV030Banner in that case (REQ-VG-REPLAY-001
    AC-3).

    A replay carries the raw STT/LLM/TTS payloads of the call, so a
    tenant-bound caller reading another tenant's session gets the same 404
    it would get for a session id that does not exist.
    """
    if gateway.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")
    await require_visible_session(gateway.storage, session_id, principal)
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        events = await replay.read_full_replay(db, session_id)
    return {
        "session_id": session_id,
        "events": [dataclasses.asdict(e) for e in events],
    }


@router.delete(
    "/sessions/{session_id}/replay",
    dependencies=[Depends(require_scope(ADMIN_SCOPE))],
)
async def delete_session_replay(
    session_id: str, gateway: Gateway = Depends(get_gateway)
) -> dict[str, Any]:
    """Delete every replay row tied to a session (REQ-VG-REPLAY-006 AC-3).

    Cascade across all four ``replay_*`` tables in one transaction.
    Returns the total row count deleted (across modalities) so the
    caller can confirm the cleanup landed.

    **Auth.** This route carried no dependency at all while its GET sibling
    was gated, so the one verb that destroys the captured payloads of a call
    was the one verb anyone could reach. It takes
    ``require_scope(ADMIN_SCOPE)``, the scope every write on a dashboard
    router takes. The gate is a no-op while no API keys are configured, so
    the self-hosted default is unchanged.

    No tenant check on top of the scope: an admin principal is not narrowed
    by tenant anywhere in this codebase, and the only key that clears the
    admin scope is an admin key.
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
    principal: Principal = Depends(require_principal),
) -> dict[str, Any]:
    """Return per-project replay storage breakdown.

    Sums ``sessions.replay_size_bytes`` per project (the column is
    populated by T08's ``finalize_session_replay``). Sessions without
    captured replay (NULL replay_size_bytes) contribute 0 to the sum.
    The Refinery requires the dashboard surface this so "the cost is
    not invisible" to the developer.

    **Auth and tenant scoping.** This read had no dependency: it aggregates
    every session row on the deployment, so it named other tenants' projects
    and sized their captured traffic to anyone who could reach the port. It
    takes :func:`require_principal`, the dependency the sibling dashboard
    reads take, and the tenant resolved from that principal becomes a
    predicate on the query. The predicate is the one the repositories use
    (``session_repository.list_sessions``): ``None`` means every row (the
    operator/admin case, unchanged), ``""`` means the unattributed bucket,
    i.e. ``tenant_id IS NULL``. Filtering in SQL rather than post-hoc keeps
    ``total_replay_size_bytes`` consistent with ``by_project``: a total
    computed over rows the caller cannot see would leak the size back.
    """
    if gateway.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")
    tenant = resolve_read_tenant(principal, None)
    conditions = ["replay_size_bytes IS NOT NULL"]
    params: dict[str, Any] = {}
    if tenant is not None:
        if tenant == "":
            conditions.append("tenant_id IS NULL")
        else:
            conditions.append("tenant_id = :tenant")
            params["tenant"] = tenant
    where = " AND ".join(conditions)
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        result = await db.execute(
            text(
                "SELECT project, COALESCE(SUM(replay_size_bytes), 0) "
                "FROM sessions "
                f"WHERE {where} "
                "GROUP BY project "
                "ORDER BY project ASC"
            ),
            params,
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


@router.post(
    "/projects/{project_id}/replay/retention",
    dependencies=[Depends(require_scope(ADMIN_SCOPE))],
)
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

    **Auth.** This route carried no dependency at all: it edits runtime
    config, and the config it edits decides how long captured payloads
    survive, so an anonymous caller could shorten a project's window to a
    day and let the retention worker do the deleting. It takes
    ``require_scope(ADMIN_SCOPE)``, the scope every write on a dashboard
    router takes, declared on the ROUTE and not the router because the two
    reads on this router must stay reachable by a read-scoped operator. The
    gate is a no-op while no API keys are configured.
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
