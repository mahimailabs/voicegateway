"""Provider endpoints under /v1/providers (list, CRUD, test)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from voicegateway.core import registry as _registry
from voicegateway.core.crypto import decrypt, mask
from voicegateway.server.api._deps import get_gateway, require_scope

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(prefix="/providers", tags=["providers"])
write_dep = Depends(require_scope("write"))
logger = logging.getLogger(__name__)


@router.get("")
async def list_providers(gateway: Gateway = Depends(get_gateway)) -> dict:
    result = []
    for name, pcfg in gateway.config.providers.items():
        api_key = pcfg.get("api_key", "") if isinstance(pcfg, dict) else ""
        source = pcfg.get("_source", "yaml") if isinstance(pcfg, dict) else "yaml"
        result.append(
            {
                "provider_id": name,
                "source": source,
                "api_key_masked": mask(api_key) if source != "db" else mask(api_key),
                "base_url": pcfg.get("base_url") if isinstance(pcfg, dict) else None,
            }
        )
    return {"providers": result}


@router.post("", dependencies=[write_dep])
async def create_provider_endpoint(
    body: dict[str, Any],
    gateway: Gateway = Depends(get_gateway),
) -> dict:
    pid = body.get("provider_id", "")
    ptype = body.get("provider_type", pid)
    api_key = body.get("api_key", "")
    base_url = body.get("base_url")
    project = body.get("project")

    if not pid or not isinstance(pid, str):
        raise HTTPException(400, "provider_id is required and must be a string")
    if ptype not in _registry._PROVIDER_REGISTRY:
        raise HTTPException(400, f"Unknown provider_type '{ptype}'")
    if pid in gateway.config.providers:
        is_managed = (
            isinstance(gateway.config.providers[pid], dict)
            and gateway.config.providers[pid].get("_source") == "db"
        )
        if not is_managed:
            raise HTTPException(409, f"Provider '{pid}' already exists in YAML")

    if project:
        existing_project = gateway.config.projects.get(project)
        if existing_project is not None and ptype in existing_project.providers:
            yaml_entry = existing_project.providers[ptype]
            yaml_pinned = (
                not isinstance(yaml_entry, dict) or yaml_entry.get("_source") != "db"
            )
            if yaml_pinned:
                raise HTTPException(
                    409,
                    f"Provider '{ptype}' is already pinned in YAML at "
                    f"projects.{project}.providers.{ptype}; "
                    "remove it from voicegw.yaml before creating a "
                    "managed override.",
                )
    if gateway.storage is None:
        raise HTTPException(400, "Storage not enabled")

    await gateway.storage.upsert_managed_provider(
        pid, ptype, api_key, base_url, project=project
    )
    audit_body = {**body, "api_key": "<redacted>"} if "api_key" in body else body
    await gateway.storage.log_audit_event("provider", pid, "create", audit_body, "api")
    await gateway.refresh_config()

    return {
        "provider_id": pid,
        "source": "db",
        "api_key_masked": mask(api_key),
        "project": project,
    }


@router.patch("/{provider_id}", dependencies=[write_dep])
async def update_provider(
    provider_id: str,
    body: dict[str, Any],
    gateway: Gateway = Depends(get_gateway),
) -> dict:
    if gateway.storage is None:
        raise HTTPException(400, "Storage not enabled")
    existing = await gateway.storage.get_managed_provider(provider_id)
    if existing is None:
        raise HTTPException(404, f"No managed provider '{provider_id}'")

    current_key = decrypt(existing.get("api_key_encrypted", ""))
    api_key = body.get("api_key", current_key)
    base_url = body.get("base_url", existing.get("base_url"))
    ptype = body.get("provider_type", existing["provider_type"])
    if ptype not in _registry._PROVIDER_REGISTRY:
        raise HTTPException(400, f"Unknown provider_type '{ptype}'")

    project = body.get("project", existing.get("project"))

    await gateway.storage.upsert_managed_provider(
        provider_id, ptype, api_key, base_url, project=project
    )
    audit_body = {**body, "api_key": "<redacted>"} if "api_key" in body else body
    await gateway.storage.log_audit_event(
        "provider", provider_id, "update", audit_body, "api"
    )
    await gateway.refresh_config()
    return {"provider_id": provider_id, "updated": True}


@router.delete("/{provider_id}", dependencies=[write_dep])
async def delete_provider(
    provider_id: str,
    confirm: bool = Query(False),
    gateway: Gateway = Depends(get_gateway),
) -> dict:
    if gateway.storage is None:
        raise HTTPException(400, "Storage not enabled")
    managed = await gateway.storage.get_managed_provider(provider_id)
    if managed is None:
        if provider_id in gateway.config.providers:
            raise HTTPException(
                403,
                f"Provider '{provider_id}' is YAML-defined and cannot be deleted",
            )
        raise HTTPException(404, f"No provider '{provider_id}'")

    if not confirm:
        return {"would_delete": {"provider_id": provider_id}}
    await gateway.storage.delete_managed_provider(provider_id)
    await gateway.storage.log_audit_event(
        "provider", provider_id, "delete", None, "api"
    )
    await gateway.refresh_config()
    return {"deleted": provider_id}


async def _resolve_test_target(
    gateway: Gateway,
    provider_id: str,
) -> tuple[str | None, dict[str, Any] | None]:
    """Return ``(provider_type, provider_config)`` for the test path."""
    cfg = gateway.config

    if provider_id in cfg.providers:
        pcfg = cfg.providers[provider_id]
        if isinstance(pcfg, dict) and pcfg.get("_source") != "db":
            return provider_id, dict(pcfg)

    if gateway.storage is not None:
        row = await gateway.storage.get_managed_provider(provider_id)
        if row is not None:
            return row["provider_type"], {
                "api_key": decrypt(row.get("api_key_encrypted", "")),
                "base_url": row.get("base_url"),
                **(row.get("extra_config") or {}),
            }

    if ":" in provider_id:
        project, _, provider_type = provider_id.partition(":")
        project_cfg = cfg.projects.get(project)
        if project_cfg is not None and provider_type in project_cfg.providers:
            return provider_type, dict(project_cfg.providers[provider_type])

    return None, None


@router.post("/{provider_id}/test", dependencies=[write_dep])
async def test_provider(
    provider_id: str,
    gateway: Gateway = Depends(get_gateway),
) -> dict:
    ptype, pcfg = await _resolve_test_target(gateway, provider_id)
    if pcfg is None:
        raise HTTPException(404, f"No provider '{provider_id}'")
    if ptype not in _registry._PROVIDER_REGISTRY:
        return {
            "status": "failed",
            "message": f"Unknown type '{ptype}'",
            "latency_ms": 0,
        }
    try:
        inst = _registry.create_provider(ptype, pcfg)
        start = time.time()
        ok = await asyncio.wait_for(inst.health_check(), timeout=10.0)
        latency_ms = int((time.time() - start) * 1000)
    except TimeoutError:
        return {
            "status": "failed",
            "message": "Provider health check timed out",
            "latency_ms": 10000,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Provider test for '%s' failed: %s", provider_id, exc)
        return {
            "status": "failed",
            "message": "Provider health check failed",
            "latency_ms": 0,
        }
    return {"status": "ok" if ok else "failed", "latency_ms": latency_ms}


@router.post("/test", dependencies=[write_dep])
async def test_provider_stateless(body: dict[str, Any]) -> dict:
    """Stateless health check: takes provider_type + api_key + base_url."""
    ptype = body.get("provider_type", "")
    if ptype not in _registry._PROVIDER_REGISTRY:
        return {
            "status": "failed",
            "message": f"Unknown provider_type '{ptype}'",
            "latency_ms": 0,
        }
    cfg: dict[str, Any] = {
        "api_key": body.get("api_key", ""),
        "base_url": body.get("base_url"),
    }
    try:
        inst = _registry.create_provider(ptype, cfg)
        start = time.time()
        ok = await asyncio.wait_for(inst.health_check(), timeout=10.0)
        latency_ms = int((time.time() - start) * 1000)
    except TimeoutError:
        return {
            "status": "failed",
            "message": "Provider health check timed out",
            "latency_ms": 10000,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Stateless provider test for '%s' failed: %s", ptype, exc)
        return {
            "status": "failed",
            "message": "Provider health check failed",
            "latency_ms": 0,
        }
    return {"status": "ok" if ok else "failed", "latency_ms": latency_ms}
