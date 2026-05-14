"""Guardrail event endpoints under /v1/guardrails."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from voicegateway.repository import guardrail_events_repository as guardrail_events
from voicegateway.schemas.guardrail_policy_schema import (
    ACTIVE_GUARDRAIL_ACTIONS,
    GUARDRAIL_CATEGORIES,
)
from voicegateway.server.api._deps import get_gateway

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(prefix="/guardrails", tags=["guardrails"])


def _guardrail_since(days: int) -> str:
    days = max(1, min(days, 365))
    return (datetime.now(tz=UTC) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _validate_event_filter(*, category: str | None, action: str | None) -> None:
    if category is not None and category not in GUARDRAIL_CATEGORIES:
        allowed = ", ".join(GUARDRAIL_CATEGORIES)
        raise HTTPException(
            status_code=400,
            detail=f"unknown guardrail category: {category}; allowed: {allowed}",
        )
    if action is not None and action not in ACTIVE_GUARDRAIL_ACTIONS:
        allowed = ", ".join(ACTIVE_GUARDRAIL_ACTIONS)
        raise HTTPException(
            status_code=400,
            detail=f"unknown guardrail action: {action}; allowed: {allowed}",
        )


@router.get("/events")
async def list_events(
    days: int = Query(7, ge=1, le=365),
    project: str | None = Query(None),
    tenant: str | None = Query(None),
    category: str | None = Query(None),
    action: str | None = Query(None),
    event_type: str | None = Query(None, pattern="^(fired|bypassed)$"),
    limit: int = Query(100, ge=1, le=1000),
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    if gateway.storage is None:
        return {"events": [], "filter": {"project": project, "tenant": tenant}}
    _validate_event_filter(category=category, action=action)
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        rows = await guardrail_events.list_events(
            db,
            since=_guardrail_since(days),
            project=project,
            tenant=tenant,
            category=category,
            action=action,
            event_type=event_type,
            limit=limit,
        )
    return {
        "events": [dataclasses.asdict(row) for row in rows],
        "filter": {
            "days": days,
            "project": project,
            "tenant": tenant,
            "category": category,
            "action": action,
            "event_type": event_type,
        },
    }


@router.get("/aggregate")
async def aggregate_events(
    days: int = Query(7, ge=1, le=365),
    project: str | None = Query(None),
    tenant: str | None = Query(None),
    category: str | None = Query(None),
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    if gateway.storage is None:
        return {"counts": [], "top_sessions": []}
    _validate_event_filter(category=category, action=None)
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        since = _guardrail_since(days)
        counts = await guardrail_events.aggregate_counts(
            db, since=since, project=project, tenant=tenant
        )
        top_sessions = (
            await guardrail_events.top_sessions_by_category(
                db,
                category=category,
                since=since,
                project=project,
                tenant=tenant,
            )
            if category
            else []
        )
    return {
        "counts": [dataclasses.asdict(row) for row in counts],
        "top_sessions": [dataclasses.asdict(row) for row in top_sessions],
        "filter": {
            "days": days,
            "project": project,
            "tenant": tenant,
            "category": category,
        },
    }
