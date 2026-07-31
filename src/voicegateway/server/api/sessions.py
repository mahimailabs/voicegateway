"""Session endpoints: GET /v1/sessions, GET /v1/sessions/{id}.

These are the public twin of the dashboard reads under ``/api/sessions``:
same storage calls, same rows. They took only ``get_gateway``, so everything
gated and tenant-scoped on ``/api/sessions`` was still served unauthenticated
and unscoped one prefix over, which makes the gate on the mirror worth
nothing to anyone who can spell ``/v1``.

Auth is declared ONCE, on the router, the idiom
``dashboard/sessions.py`` and ``dashboard/calls.py`` give: both routes here
are reads and a per-handler dependency is a thing a later handler can forget.
:func:`require_principal` is the dependency the sibling *reads* use;
``require_scope(ADMIN_SCOPE)`` is reserved for the routers that spend money or
touch infra, and reading rows already on disk does neither. The gate is a
no-op while no API keys are configured (the self-hosted single-operator
default), so nothing changes for an operator who has not enabled auth. The
handlers keep a ``principal`` parameter as well because they need the identity
itself; FastAPI caches a dependency per request, so the key is verified once.

Tenant scoping reuses the helpers
:mod:`voicegateway.server.api.dashboard.sessions` added rather than restating
them, so the two surfaces cannot drift: the list filters at the query through
``resolve_read_tenant``, and the detail checks ``_visible`` on the row it has
already fetched. A foreign session returns the same 404, with the same body,
as a session id that does not exist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query

from voicegateway.server.api._deps import (
    Principal,
    get_gateway,
    require_principal,
    resolve_read_tenant,
)
from voicegateway.server.api.dashboard.sessions import _not_found, _visible

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(
    prefix="/sessions",
    tags=["sessions"],
    dependencies=[Depends(require_principal)],
)


@router.get("")
async def list_sessions(
    limit: int = Query(100, ge=1, le=1000),
    project: str | None = Query(None),
    order_by: str = Query(
        "started_at_desc",
        pattern="^(started_at_desc|started_at_asc|cost_desc|cost_asc)$",
    ),
    gateway: Gateway = Depends(get_gateway),
    principal: Principal = Depends(require_principal),
) -> list[dict]:
    """Return recent voice sessions, ordered per ``order_by``.

    Scoped to the authenticated principal: the tenant comes from the key, not
    from a caller-supplied parameter (this route publishes none), so a
    tenant-bound key never sees another tenant's sessions in the list. An
    operator with no credential, a static config key, or an admin key resolves
    to ``None`` and keeps listing every tenant, unchanged.

    Filtering here and not only on the detail route matters: a list that
    returned every tenant's sessions hands over the ids that the detail route
    is refusing.
    """
    tenant = resolve_read_tenant(principal, None)
    if gateway.storage is None:
        return []
    return await gateway.storage.list_sessions(
        limit=limit, project=project, order_by=order_by, tenant=tenant
    )


@router.get("/{session_id}")
async def session_detail(
    session_id: str,
    gateway: Gateway = Depends(get_gateway),
    principal: Principal = Depends(require_principal),
) -> dict:
    """Return one session by id with a per-modality cost breakdown.

    A session belonging to another tenant gets the same 404 as a session id
    that does not exist, checked on the row already fetched here (no second
    query). See ``dashboard.sessions.require_visible_session`` for why it is a
    404 and not a 403: a 403 would confirm the id is real.
    """
    if gateway.storage is None:
        raise _not_found(session_id)
    row = await gateway.storage.get_session(session_id)
    if row is None or not _visible(row, resolve_read_tenant(principal, None)):
        raise _not_found(session_id)
    return row
