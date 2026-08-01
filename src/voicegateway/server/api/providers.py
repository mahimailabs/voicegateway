"""Provider endpoints under /v1/providers (list, CRUD, test)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query

from voicegateway.core import registry as _registry
from voicegateway.core.crypto import decrypt, mask
from voicegateway.server.api._deps import get_gateway, require_scope

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(prefix="/providers", tags=["providers"])
write_dep = Depends(require_scope("write"))
logger = logging.getLogger(__name__)

# The operator allowlist for provider base_url hosts. Declared on ServeConfig in
# schemas/config_schema.py; read here off the raw ``serve:`` block because that
# is what GatewayConfig carries at runtime.
_HOSTS_CONFIG_KEY = "provider_base_url_hosts"
_HOSTS_CONFIG_PATH = f"serve.{_HOSTS_CONFIG_KEY}"

# The host each provider module already treats as its own endpoint, mirroring
# the literals in voicegateway/inference/providers/*.py. Keeping these permitted
# by default is what makes an unset allowlist a no-op for deployments that only
# ever point a provider at its own vendor.
_DEFAULT_PROVIDER_HOSTS: dict[str, tuple[str, ...]] = {
    "openai": ("api.openai.com",),
    "anthropic": ("api.anthropic.com",),
    "deepgram": ("api.deepgram.com",),
    "cartesia": ("api.cartesia.ai",),
    "elevenlabs": ("api.elevenlabs.io",),
    "assemblyai": ("api.assemblyai.com",),
    "groq": ("api.groq.com",),
    "ollama": ("localhost",),
}


def _url_host(value: Any) -> str | None:
    """Return the lowercase host of a base URL, or None when there is none."""
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if "//" not in candidate:
        # Bare "api.example.com/v1" parses as a path without this.
        candidate = "//" + candidate
    try:
        host = urlparse(candidate).hostname
    except ValueError:
        return None
    return host.lower() if host else None


def _allowlisted_hosts(gateway: Gateway) -> set[str]:
    """Return the hosts the operator configured under ``serve:``."""
    serve_cfg: Any = getattr(gateway.config, "serve", None)
    if isinstance(serve_cfg, dict):
        raw = serve_cfg.get(_HOSTS_CONFIG_KEY)
    else:
        raw = getattr(serve_cfg, _HOSTS_CONFIG_KEY, None)
    if not isinstance(raw, list):
        return set()
    hosts = {_url_host(entry) for entry in raw}
    return {host for host in hosts if host is not None}


def _guard_base_url_host(
    gateway: Gateway,
    provider_id: str,
    provider_type: str,
    current_base_url: Any,
    new_base_url: Any,
) -> None:
    """Reject repointing a stored key at a host nobody approved.

    Only called when the update keeps the already-stored key: with the key in
    the same request there is nothing to leak, so the host stays unconstrained.
    """
    new_host = _url_host(new_base_url)
    if new_host is None:
        # Cleared or absent base_url falls back to the provider's own default.
        return

    permitted = _allowlisted_hosts(gateway)
    permitted.update(_DEFAULT_PROVIDER_HOSTS.get(provider_type, ()))
    current_host = _url_host(current_base_url)
    if current_host is not None:
        permitted.add(current_host)

    if new_host in permitted:
        return

    raise HTTPException(
        400,
        f"base_url host '{new_host}' is not permitted for provider "
        f"'{provider_id}'. Moving base_url to a new host while reusing the "
        "stored API key would send that key to the new host. Add the host to "
        f"'{_HOSTS_CONFIG_PATH}' in voicegw.yaml, or send a fresh 'api_key' "
        "in this request.",
    )


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

    # The dangerous shape is "new host + stored key": POST /test would then ship
    # the operator's key to a host the caller picked. A request that carries its
    # own api_key owns a key already, and an empty stored key is not a secret, so
    # both stay unconstrained. Default hosts come from the STORED provider_type,
    # the vendor that issued the stored key, not from a caller-supplied one.
    supplied_key = body.get("api_key")
    reuses_stored_key = bool(current_key) and not (
        isinstance(supplied_key, str) and supplied_key
    )
    if reuses_stored_key:
        _guard_base_url_host(
            gateway,
            provider_id,
            str(existing["provider_type"]),
            existing.get("base_url"),
            base_url,
        )

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
