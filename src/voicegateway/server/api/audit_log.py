"""Audit-log endpoint: GET /v1/audit-log."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query

from voicegateway.server.api._deps import get_gateway

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(prefix="/audit-log", tags=["audit-log"])


@router.get("")
async def list_audit_log(
    limit: int = Query(50, ge=1, le=500),
    entity_type: str | None = Query(None),
    entity_id: str | None = Query(None),
    action: str | None = Query(None),
    gateway: Gateway = Depends(get_gateway),
) -> list[dict]:
    if gateway.storage is None:
        return []
    return await gateway.storage.get_audit_log(
        limit=limit,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
    )
