"""Model catalog endpoints: GET, POST, DELETE under /v1/models."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from voicegateway.server.api._deps import get_gateway, require_scope

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(prefix="/models", tags=["models"])
write_dep = Depends(require_scope("write"))


@router.get("")
async def list_models(
    project: str | None = Query(None),
    gateway: Gateway = Depends(get_gateway),
) -> dict:
    cfg = gateway.config
    models: dict[str, dict[str, Any]] = {}
    for modality, modality_models in cfg.models.items():
        if not isinstance(modality_models, dict):
            continue
        for model_id, model_cfg in modality_models.items():
            if not isinstance(model_cfg, dict):
                continue
            models[model_id] = {
                "modality": modality,
                "provider": model_cfg.get("provider", ""),
                "model": model_cfg.get("model", ""),
            }
    if project:
        pcfg = cfg.get_project(project)
        if pcfg and pcfg.default_stack and pcfg.default_stack in cfg.stacks:
            wanted = set(cfg.stacks[pcfg.default_stack].values())
            models = {k: v for k, v in models.items() if k in wanted}
    return {"models": models, "project": project}


@router.post("", dependencies=[write_dep])
async def create_model(
    body: dict[str, Any],
    gateway: Gateway = Depends(get_gateway),
) -> dict:
    if gateway.storage is None:
        raise HTTPException(400, "Storage not enabled")
    modality = body.get("modality", "")
    provider_id = body.get("provider_id", "")
    model_name = body.get("model_name", "")
    model_id = f"{provider_id}/{model_name}"

    if provider_id not in gateway.config.providers:
        raise HTTPException(400, f"Provider '{provider_id}' not configured")
    yaml_bucket = gateway.config.models.get(modality, {})
    if model_id in yaml_bucket:
        raise HTTPException(409, f"Model '{model_id}' already exists")

    await gateway.storage.upsert_managed_model(
        model_id=model_id,
        modality=modality,
        provider_id=provider_id,
        model_name=model_name,
        display_name=body.get("display_name"),
        default_language=body.get("default_language"),
        default_voice=body.get("default_voice"),
        extra_config=body.get("config"),
    )
    await gateway.storage.log_audit_event("model", model_id, "create", body, "api")
    await gateway.refresh_config()
    return {"model_id": model_id, "source": "db", "created": True}


@router.delete("/{model_id:path}", dependencies=[write_dep])
async def delete_model(
    model_id: str,
    confirm: bool = Query(False),
    gateway: Gateway = Depends(get_gateway),
) -> dict:
    if gateway.storage is None:
        raise HTTPException(400, "Storage not enabled")
    managed = await gateway.storage.get_managed_model(model_id)
    if managed is None:
        for mm in gateway.config.models.values():
            if isinstance(mm, dict) and model_id in mm:
                raise HTTPException(403, f"Model '{model_id}' is YAML-defined")
        raise HTTPException(404, f"No model '{model_id}'")
    if not confirm:
        return {"would_delete": {"model_id": model_id}}
    await gateway.storage.delete_managed_model(model_id)
    await gateway.storage.log_audit_event("model", model_id, "delete", None, "api")
    await gateway.refresh_config()
    return {"deleted": model_id}
