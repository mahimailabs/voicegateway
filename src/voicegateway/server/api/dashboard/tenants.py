"""Dashboard endpoints: GET /api/tenants, /api/tenants/{id}.

v0.4.0 multi-tenant API surface (REQ-VG-TENANT-002 + REQ-VG-TENANT-003).
Tenants are derived from DISTINCT sessions.tenant_id; the dashboard's
filter feed and per-tenant overview are read-only here.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from voicegateway.repository import (
    tenants_repository as tenants,
)
from voicegateway.server.api._deps import get_gateway

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(prefix="/tenants", tags=["dashboard"])


@router.get("")
async def list_tenants_endpoint(
    limit: int = Query(50, ge=1, le=1000),
    q: str | None = Query(None, max_length=128),
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Return the tenant index for the dashboard filter typeahead.

    ``q`` is a substring match against tenant_id (``%`` and ``_``
    escaped to literal characters). The implicit unattributed bucket
    (NULL tenant_id) is included as a separate ``unattributed`` field
    so the FE can render it as a muted pill below the tenant list.
    """
    if gateway.storage is None:
        return {
            "tenants": [],
            "unattributed": {
                "session_count": 0,
                "total_cost_usd": 0.0,
                "first_seen": None,
                "last_seen": None,
            },
        }
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        rows = await tenants.list_tenants(db, limit=limit, query=q)
        u = await tenants.get_unattributed_aggregates(db)
    return {
        "tenants": [dataclasses.asdict(r) for r in rows],
        "unattributed": dataclasses.asdict(u),
    }


@router.get("/{tenant_id}")
async def get_tenant_endpoint(
    tenant_id: str, gateway: Gateway = Depends(get_gateway)
) -> dict[str, Any]:
    """Return aggregates for a single tenant. 404 when unseen."""
    if gateway.storage is None:
        raise HTTPException(status_code=404, detail=f"Tenant {tenant_id!r} not found")
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        row = await tenants.get_tenant(db, tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Tenant {tenant_id!r} not found")
    return dataclasses.asdict(row)
