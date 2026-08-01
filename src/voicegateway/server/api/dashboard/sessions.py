"""Dashboard endpoints under /api/sessions.

- GET /api/sessions: list recent sessions
- GET /api/sessions/{id}: single-session details
- GET /api/sessions/{id}/turns: per-turn rows
- GET /api/sessions/{id}/transcript: per-turn call transcript
- GET /api/sessions/{id}/dead_air: dead-air events for one session

The replay endpoints on /api/sessions/{id}/replay live in
:mod:`voicegateway.server.api.dashboard.replay` to keep the replay-
related Pydantic shapes and the StaticFiles mount they share local.

Auth is declared ONCE, on the router, for the reason
``dashboard/calls.py`` gives: a per-handler dependency is a thing a later
handler can forget, and the list endpoint carried one while the four
per-session reads did not. :func:`require_principal` is the dependency the
sibling dashboard *reads* use (``dashboard/costs.py``, ``dashboard/calls.py``);
``require_scope(ADMIN_SCOPE)`` is reserved for the routers that spend money or
touch infra, and reading rows already on disk does neither. The list endpoint
keeps its handler-level parameter as well: FastAPI caches a dependency per
request, so the key is verified once, not twice, and the scope it requires is
unchanged.

Tenant scoping is enforced AFTER the fetch, through
:func:`require_visible_session`, rather than by adding a tenant predicate to
``session_repository.get_session``: the row already carries ``tenant_id``
(``row_to_session``), so no query changes and every caller of that repo keeps
its current behavior.
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
    from voicegateway.services.storage_service import StorageService

router = APIRouter(
    prefix="/sessions",
    tags=["dashboard"],
    dependencies=[Depends(require_principal)],
)


def _visible(row: dict[str, Any], tenant: str | None) -> bool:
    """Whether a session row is readable by a principal scoped to ``tenant``.

    ``None`` is the operator/admin case: every row. ``""`` is the unattributed
    bucket, i.e. ``tenant_id IS NULL``, the same convention the repositories use
    for their SQL tenant predicate (``session_repository.list_sessions``) and the
    same helper shape ``dashboard/calls.py`` uses for call rows.
    """
    if tenant is None:
        return True
    stored: str | None = row["tenant_id"]
    if tenant == "":
        return stored is None
    return stored == tenant


def _not_found(session_id: str) -> HTTPException:
    """The single 404 every unreadable session id gets."""
    return HTTPException(status_code=404, detail=f"Session '{session_id}' not found")


async def require_visible_session(
    storage: StorageService, session_id: str, principal: Principal
) -> None:
    """Raise 404 unless ``principal`` may read ``session_id``.

    The per-session sub-resources (turns, transcript, dead air, replay) query
    their own tables by ``session_id`` and never see the session row, so the
    tenant check needs one lookup of the parent session. It is skipped entirely
    for the operator/admin principal (``tenant is None``), which keeps the
    self-hosted default free of an extra query AND preserves the pre-existing
    contract that an unknown id yields an empty payload rather than a 404.

    For a tenant-bound key, "this session is another tenant's" and "this session
    does not exist" are deliberately the same 404. A 403 on the foreign case
    would confirm the id exists, which is the fact being protected.

    Imported by :mod:`voicegateway.server.api.dashboard.replay`, whose /replay
    read is the fifth endpoint under this session id.
    """
    tenant = resolve_read_tenant(principal, None)
    if tenant is None:
        return
    row = await storage.get_session(session_id)
    if row is None or not _visible(row, tenant):
        raise _not_found(session_id)


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
    session_id: str,
    gateway: Gateway = Depends(get_gateway),
    principal: Principal = Depends(require_principal),
) -> dict[str, Any]:
    """Return one session by id with per-modality breakdown.

    Mirror of /v1/sessions/{id}: returns the session row plus the
    ``by_modality`` and ``providers`` fields the storage method
    computes via a join on the requests table. 404 when no row
    matches.

    A session belonging to another tenant gets that same 404, checked on the
    row already fetched here (no second query). See
    :func:`require_visible_session` for why it is a 404 and not a 403.
    """
    if gateway.storage is None:
        raise _not_found(session_id)
    row = await gateway.storage.get_session(session_id)
    if row is None or not _visible(row, resolve_read_tenant(principal, None)):
        raise _not_found(session_id)
    return row


@router.get("/{session_id}/turns")
async def get_session_turns(
    session_id: str,
    gateway: Gateway = Depends(get_gateway),
    principal: Principal = Depends(require_principal),
) -> dict[str, Any]:
    """Return ordered per-turn rows for a session (REQ-VG-METRICS-002).

    Used by the Metrics page's session-drill-down (T13). The shape
    mirrors the ``TurnRow`` dataclass; ``agent_speak_*`` and
    ``response_speed_ms`` are null when the agent never spoke for that
    turn (T02 contract).

    404 for a tenant-bound caller reading another tenant's session.
    """
    if gateway.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")
    await require_visible_session(gateway.storage, session_id, principal)
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        rows = await turns.list_turns_by_session(db, session_id)
    return {
        "session_id": session_id,
        "turns": [dataclasses.asdict(t) for t in rows],
    }


@router.get("/{session_id}/transcript")
async def get_session_transcript(
    session_id: str,
    gateway: Gateway = Depends(get_gateway),
    principal: Principal = Depends(require_principal),
) -> dict[str, Any]:
    """Return the ordered per-turn transcript for a call.

    Populated when transcript capture is on (``attach(transcript=True)``, the
    default). Returns an empty list (not a 404) when a call has no captured
    transcript, so the UI can show a clean empty state.

    This is the most sensitive payload on the router (it is what the caller
    said), so the tenant check is the same one the other per-session reads
    take: 404 for a tenant-bound caller reading another tenant's session.
    """
    if gateway.storage is None:
        return {"session_id": session_id, "turns": []}
    await require_visible_session(gateway.storage, session_id, principal)
    turns = await gateway.storage.get_transcript(session_id)
    return {"session_id": session_id, "turns": turns}


@router.get("/{session_id}/dead_air")
async def get_session_dead_air(
    session_id: str,
    gateway: Gateway = Depends(get_gateway),
    principal: Principal = Depends(require_principal),
) -> dict[str, Any]:
    """Return dead-air events for a session (REQ-VG-METRICS-004).

    Used by the Metrics page's DeadAirList drill-down (T14). Events
    are ordered by ``started_at_ms`` ASC, matching the chronological
    rendering the FE expects.

    404 for a tenant-bound caller reading another tenant's session.
    """
    if gateway.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")
    await require_visible_session(gateway.storage, session_id, principal)
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        events = await dead_air.list_events_by_session(db, session_id)
    return {
        "session_id": session_id,
        "events": [dataclasses.asdict(e) for e in events],
    }
