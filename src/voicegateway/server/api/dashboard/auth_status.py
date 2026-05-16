"""Dashboard endpoint: GET /api/auth-status."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from voicegateway.core.auth import load_api_keys
from voicegateway.server.api._deps import get_gateway

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(tags=["dashboard"])


@router.get("/auth-status")
async def get_auth_status(gateway: Gateway = Depends(get_gateway)) -> dict:
    """Report whether the gateway requires a token for mutating endpoints.

    Used by the frontend to decide whether to show the login gate. The
    response never exposes key material.
    """
    keys = load_api_keys(gateway.config.auth)
    return {"auth_required": bool(keys)}
