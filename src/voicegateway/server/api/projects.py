"""Project endpoints under /v1/projects (list, detail, CRUD, guardrails)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError

from voicegateway.schemas.guardrail_policy_schema import (
    GUARDRAIL_CATEGORY_DESCRIPTIONS,
    GuardrailPolicy,
)
from voicegateway.server.api._deps import get_gateway, require_scope

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(prefix="/projects", tags=["projects"])
write_dep = Depends(require_scope("write"))


@router.get("")
async def list_projects(gateway: Gateway = Depends(get_gateway)) -> dict:
    """List configured projects with today's stats."""
    projects = gateway.list_projects()
    stats: dict[str, Any] = {}
    if gateway.storage is not None:
        for p in projects:
            pid = p["id"]
            stats[pid] = await gateway.storage.get_project_stats(pid)
    return {"projects": projects, "stats": stats}


@router.get("/{project_id}")
async def project_detail(
    project_id: str,
    gateway: Gateway = Depends(get_gateway),
) -> dict:
    pcfg = gateway.config.get_project(project_id)
    if pcfg is None:
        return {"error": f"project not found: {project_id}"}
    data: dict[str, Any] = {
        "id": pcfg.id,
        "name": pcfg.name,
        "description": pcfg.description,
        "daily_budget": pcfg.daily_budget,
        "budget_action": pcfg.budget_action,
        "default_stack": pcfg.default_stack,
        "tags": list(pcfg.tags),
        "accent": pcfg.accent,
        "today_spend": 0.0,
        "budget_status": "ok",
    }
    if gateway.storage is not None:
        data["today"] = await gateway.storage.get_project_stats(project_id)
        costs_today = await gateway.storage.get_cost_summary(
            "today", project=project_id
        )
        data["costs_today"] = costs_today
        today_spend = costs_today.get("total", 0.0)
        data["today_spend"] = today_spend
        enforcer = gateway._budget_enforcer
        data["budget_status"] = enforcer.get_budget_status(project_id, today_spend)
    return data


@router.get("/{project_id}/guardrails")
async def project_guardrails(
    project_id: str,
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    pcfg = gateway.config.get_project(project_id)
    if pcfg is None:
        raise HTTPException(404, f"project not found: {project_id}")
    return {
        "project_id": project_id,
        "policy": pcfg.guardrails.to_storage_dict(),
        "categories": [
            {
                "id": category,
                "description": GUARDRAIL_CATEGORY_DESCRIPTIONS[category],
            }
            for category in GUARDRAIL_CATEGORY_DESCRIPTIONS
        ],
    }


@router.post("/{project_id}/guardrails", dependencies=[write_dep])
async def update_project_guardrails(
    project_id: str,
    body: dict[str, Any],
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    if gateway.storage is None:
        raise HTTPException(400, "Storage not enabled")
    pcfg = gateway.config.get_project(project_id)
    if pcfg is None:
        raise HTTPException(404, f"project not found: {project_id}")
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


@router.post("", dependencies=[write_dep])
async def create_project(
    body: dict[str, Any],
    gateway: Gateway = Depends(get_gateway),
) -> dict:
    if gateway.storage is None:
        raise HTTPException(400, "Storage not enabled")
    pid = body.get("project_id", "")
    if pid in gateway.config.projects:
        raise HTTPException(409, f"Project '{pid}' already exists")
    await gateway.storage.upsert_managed_project(
        project_id=pid,
        name=body.get("name", pid),
        description=body.get("description", ""),
        daily_budget=float(body.get("daily_budget", 0.0)),
        budget_action=body.get("budget_action", "warn"),
        default_stack=body.get("default_stack"),
        stt_model=body.get("stt_model"),
        llm_model=body.get("llm_model"),
        tts_model=body.get("tts_model"),
        tags=body.get("tags"),
    )
    await gateway.storage.log_audit_event("project", pid, "create", body, "api")
    await gateway.refresh_config()
    return {"project_id": pid, "source": "db", "created": True}


@router.patch("/{project_id}", dependencies=[write_dep])
async def update_project(
    project_id: str,
    body: dict[str, Any],
    gateway: Gateway = Depends(get_gateway),
) -> dict:
    if gateway.storage is None:
        raise HTTPException(400, "Storage not enabled")
    managed = await gateway.storage.get_managed_project(project_id)
    if managed is None:
        raise HTTPException(404, f"No managed project '{project_id}'")
    await gateway.storage.upsert_managed_project(
        project_id=project_id,
        name=body.get("name", managed["name"]),
        description=body.get("description", managed.get("description", "")),
        daily_budget=float(body.get("daily_budget", managed.get("daily_budget", 0.0))),
        budget_action=body.get("budget_action", managed.get("budget_action", "warn")),
        default_stack=body.get("default_stack", managed.get("default_stack")),
        stt_model=body.get("stt_model", managed.get("stt_model")),
        llm_model=body.get("llm_model", managed.get("llm_model")),
        tts_model=body.get("tts_model", managed.get("tts_model")),
        tags=body.get("tags", managed.get("tags")),
    )
    await gateway.storage.log_audit_event("project", project_id, "update", body, "api")
    await gateway.refresh_config()
    return {"project_id": project_id, "updated": True}


@router.delete("/{project_id}", dependencies=[write_dep])
async def delete_project(
    project_id: str,
    confirm: bool = Query(False),
    gateway: Gateway = Depends(get_gateway),
) -> dict:
    if gateway.storage is None:
        raise HTTPException(400, "Storage not enabled")
    managed = await gateway.storage.get_managed_project(project_id)
    if managed is None:
        if project_id in gateway.config.projects:
            raise HTTPException(403, f"Project '{project_id}' is YAML-defined")
        raise HTTPException(404, f"No project '{project_id}'")
    if not confirm:
        return {"would_delete": {"project_id": project_id}}
    await gateway.storage.delete_managed_project(project_id)
    await gateway.storage.log_audit_event("project", project_id, "delete", None, "api")
    await gateway.refresh_config()
    return {"deleted": project_id}
