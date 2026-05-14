"""Cost-aggregation endpoint: GET /v1/costs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Query, Response

from voicegateway.inference.pricing import catalog
from voicegateway.server.api._deps import get_gateway
from voicegateway.server.api._helpers import parse_iso_date

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(prefix="/costs", tags=["costs"])


@router.get("")
async def list_costs(
    response: Response,
    period: str | None = Query(None),
    project: str | None = Query(None),
    per_modality: bool = Query(False),
    include_pricing_source: bool = Query(True),
    start: str | None = Query(None),
    end: str | None = Query(None),
    gateway: Gateway = Depends(get_gateway),
) -> dict:
    pricing_sources = {
        modality: catalog.pricing_source(modality) for modality in ("llm", "stt", "tts")
    }

    period_explicit = period is not None
    effective_period = period if period is not None else "today"

    if period_explicit and (start or end):
        response.headers["Deprecation"] = (
            "period parameter is ignored when start/end are "
            "provided. Drop period from new-API calls."
        )

    start_ts = parse_iso_date(start, end_of_day=False) if start else None
    end_ts = parse_iso_date(end, end_of_day=True) if end else None
    if gateway.storage is None:
        empty: dict[str, Any] = {
            "period": effective_period,
            "project": project,
            "total": 0.0,
            "by_provider": {},
            "by_model": {},
            "by_project": {},
            "pricing_sources": pricing_sources,
        }
        if per_modality:
            empty["by_modality"] = {}
        return empty
    summary = await gateway.storage.get_cost_summary(
        effective_period,
        project=project,
        include_pricing_source=include_pricing_source,
        start_ts=start_ts,
        end_ts=end_ts,
    )
    if project is None:
        summary["by_project"] = await gateway.storage.get_cost_by_project(
            effective_period, start_ts=start_ts, end_ts=end_ts
        )
    else:
        summary["by_project"] = {}
    if per_modality:
        summary["by_modality"] = await gateway.storage.get_cost_by_modality(
            effective_period,
            project=project,
            start_ts=start_ts,
            end_ts=end_ts,
        )
    summary["pricing_sources"] = pricing_sources
    return summary
