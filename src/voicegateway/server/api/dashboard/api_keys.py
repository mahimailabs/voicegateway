"""Dashboard endpoints under /api/virtual_keys.

Virtual keys expose their plaintext exactly once at creation: the FE
shows the "save this key" modal and discards it; subsequent list
responses expose only ``key_prefix``. Revoke is soft (the row stays
for audit per OQ5).
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from voicegateway.repository import (
    virtual_keys_repository as virtual_keys,
)
from voicegateway.server.api._deps import get_gateway

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(prefix="/virtual_keys", tags=["dashboard"])


@router.get("")
async def list_virtual_keys_endpoint(
    include_revoked: bool = Query(True),
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Return all virtual keys. The plaintext key never appears here.

    Each row carries ``key_prefix`` (the 8-char visible prefix) but
    not the bcrypt hash. ``include_revoked=False`` filters out soft-
    revoked entries.
    """
    if gateway.storage is None:
        return {"keys": []}
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        rows = await virtual_keys.list_keys(db, include_revoked=include_revoked)
    return {"keys": [dataclasses.asdict(r) for r in rows]}


@router.post("")
async def create_virtual_key_endpoint(
    body: dict[str, Any] = Body(...),
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Issue a new virtual key. Returns the plaintext EXACTLY ONCE.

    Body fields:
    - ``name`` (required): human-readable label shown in the dashboard.
    - ``tenant_id`` (optional): if set, requests bearing this key
      auto-tag the session with this tenant (REQ-VG-TENANT-004).
    - ``issued_by`` (optional): free-form audit string.

    The returned ``plaintext`` is the only place the full key appears.
    The FE shows the "save this key" modal and discards it; subsequent
    list responses expose only ``key_prefix``.
    """
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="`name` is required")
    tenant_id_raw = body.get("tenant_id")
    tenant_id = str(tenant_id_raw).strip() if tenant_id_raw not in (None, "") else None
    issued_by_raw = body.get("issued_by")
    issued_by = str(issued_by_raw).strip() if issued_by_raw not in (None, "") else None
    if gateway.storage is None:
        raise HTTPException(status_code=503, detail="Storage backend not configured")
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        created = await virtual_keys.create_virtual_key(
            db, name=name, tenant_id=tenant_id, issued_by=issued_by
        )
    return {
        "id": created.id,
        "plaintext": created.plaintext,
        "row": dataclasses.asdict(created.row),
    }


@router.post("/{key_id}/revoke")
async def revoke_virtual_key_endpoint(
    key_id: int, gateway: Gateway = Depends(get_gateway)
) -> dict[str, Any]:
    """Soft-revoke a virtual key (OQ5: keeps the row for audit)."""
    if gateway.storage is None:
        raise HTTPException(status_code=503, detail="Storage backend not configured")
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        ok = await virtual_keys.revoke(db, key_id)
        if not ok:
            raise HTTPException(
                status_code=404,
                detail=f"Virtual key {key_id} not found or already revoked",
            )
        row = await virtual_keys.get_by_id(db, key_id)
    if row is None:
        # Should never happen: revoke() just returned True. Defensive.
        raise HTTPException(status_code=404, detail=f"Virtual key {key_id} vanished")
    return {"id": key_id, "revoked": True, "row": dataclasses.asdict(row)}
