"""Dashboard endpoints: GET /api/projects, GET+POST /api/projects/{id}/guardrails."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import ValidationError

from voicegateway.schemas.guardrail_policy_schema import (
    GUARDRAIL_CATEGORY_DESCRIPTIONS,
    GuardrailPolicy,
)
from voicegateway.server.api._deps import get_gateway

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(prefix="/projects", tags=["dashboard"])


@router.get("")
async def get_projects(gateway: Gateway = Depends(get_gateway)) -> dict:
    """List configured projects with today's stats."""
    projects = gateway.list_projects()
    stats: dict[str, Any] = {}
    if gateway.storage is not None:
        for p in projects:
            stats[p["id"]] = await gateway.storage.get_project_stats(p["id"])
    return {"projects": projects, "stats": stats}


@router.get("/{project_id}/guardrails")
async def get_project_guardrails(
    project_id: str, gateway: Gateway = Depends(get_gateway)
) -> dict[str, Any]:
    """Return the current guardrail policy for a project."""
    pcfg = gateway.config.get_project(project_id)
    if pcfg is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found")
    return {
        "project_id": project_id,
        "policy": pcfg.guardrails.to_storage_dict(),
        "categories": [
            {"id": category, "description": description}
            for category, description in GUARDRAIL_CATEGORY_DESCRIPTIONS.items()
        ],
    }


@router.post("/{project_id}/guardrails")
async def update_project_guardrails(
    project_id: str,
    body: dict[str, Any] = Body(...),
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Persist a project's v0.6.0 guardrail policy overlay."""
    if gateway.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")
    pcfg = gateway.config.get_project(project_id)
    if pcfg is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found")
    try:
        policy = GuardrailPolicy.from_raw(body)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await gateway.storage.set_managed_project_guardrails(
        project_id=project_id,
        policy=policy.to_storage_dict(),
        name=pcfg.name,
        description=pcfg.description,
        daily_budget=pcfg.daily_budget,
        budget_action=pcfg.budget_action,
        default_stack=pcfg.default_stack,
        tags=list(pcfg.tags),
    )
    await gateway.storage.log_audit_event(
        "project", project_id, "guardrails_update", policy.to_storage_dict(), "api"
    )
    await gateway.refresh_config()
    refreshed = gateway.config.get_project(project_id)
    return {
        "project_id": project_id,
        "policy": (
            refreshed.guardrails.to_storage_dict()
            if refreshed is not None
            else policy.to_storage_dict()
        ),
    }
