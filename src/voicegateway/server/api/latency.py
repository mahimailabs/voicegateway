"""Latency-aggregation endpoint: GET /v1/latency."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query

from voicegateway.server.api._deps import get_gateway

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(prefix="/latency", tags=["latency"])


@router.get("")
async def list_latency(
    period: str = Query("today"),
    project: str | None = Query(None),
    gateway: Gateway = Depends(get_gateway),
) -> dict:
    if gateway.storage is None:
        return {}
    pcts = gateway.config.latency.get("percentiles") or [50.0, 95.0, 99.0]
    return await gateway.storage.get_latency_stats(
        period, project=project, percentiles=pcts
    )
