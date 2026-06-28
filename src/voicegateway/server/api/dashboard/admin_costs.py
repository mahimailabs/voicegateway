"""Admin-only cross-tenant cost rollup: GET /api/admin/costs/by-tenant.

This is the ONE cross-tenant read. It is gated behind ``require_scope("admin")``
(the Task 3 admin enforcement) so a tenant key is refused with 403 BEFORE any
backend-availability check. The cross-tenant aggregation only exists on the
ClickHouse collector/cloud backend; the SQLite self-hoster is single-tenant, so
when no ClickHouse client is bound this endpoint returns 503 (an honest
boundary, not a silent empty result).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from voicegateway.clickhouse import read_repository as ch_read
from voicegateway.core.auth import ADMIN_SCOPE
from voicegateway.repository.cost_repository import resolve_window
from voicegateway.server.api._deps import require_scope

router = APIRouter(prefix="/admin", tags=["dashboard", "admin"])


@router.get("/costs/by-tenant")
async def get_costs_by_tenant(
    request: Request,
    period: str = Query("today", enum=["today", "week", "month", "all"]),
    _auth: None = Depends(require_scope(ADMIN_SCOPE)),
) -> dict[str, dict]:
    """Return ``{tenant_id: {cost, requests}}`` across every tenant.

    Authz (admin) precedes backend availability: a non-admin key is rejected
    by the ``require_scope("admin")`` dependency with 403 before this body
    runs. An admin key reaches the body and gets 503 when ClickHouse is not
    configured.
    """
    ch_client = getattr(request.app.state, "ch_client", None)
    if ch_client is None:
        raise HTTPException(
            status_code=503,
            detail="cross-tenant admin view requires ClickHouse",
        )
    since, until = resolve_window(period)
    return await ch_read.get_cost_by_tenant_admin(ch_client, since=since, until=until)
