"""Dashboard endpoint: GET /api/providers/by-project (v0.0.5)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Query

from voicegateway.core.crypto import decrypt, mask
from voicegateway.server.api._deps import get_gateway

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

# Local providers don't need an api_key to be considered "configured"; the
# /api/status handler shares this same set.
_LOCAL_PROVIDER_NAMES = frozenset({"ollama", "whisper", "kokoro", "piper"})

router = APIRouter(prefix="/providers", tags=["dashboard"])


@router.get("/by-project")
async def get_providers_by_project(
    project: str | None = Query(None),
    gateway: Gateway = Depends(get_gateway),
) -> dict:
    """Return per-project provider keys.

    Surfaces both YAML-defined ``projects.<id>.providers.<name>`` blocks
    and DB-managed managed_providers rows scoped to a project. The
    api_key is masked. When ``project=...`` is supplied, only rows
    scoped to that project are returned.
    """
    rows: list[dict[str, Any]] = []

    # 1. YAML projects.<id>.providers.<name>. After Item 1 fixed the
    # config_manager merge, ``config.projects[<id>].providers`` can
    # contain DB-managed rows alongside YAML entries; skip the DB ones
    # here so the loop below tags them with source="db" instead.
    for proj_id, proj_cfg in gateway.config.projects.items():
        for prov_name, prov_cfg in proj_cfg.providers.items():
            if not isinstance(prov_cfg, dict):
                continue
            if prov_cfg.get("_source") == "db":
                continue
            api_key = prov_cfg.get("api_key")
            rows.append(
                {
                    "project": proj_id,
                    "provider": prov_name,
                    "provider_id": f"{proj_id}:{prov_name}",
                    "source": "yaml",
                    "api_key_masked": mask(api_key) if api_key else None,
                    "base_url": prov_cfg.get("base_url"),
                    "type": "local" if prov_name in _LOCAL_PROVIDER_NAMES else "cloud",
                }
            )

    # 2. DB-managed rows whose project column is non-null. Skip rows
    # whose composite id is already covered by YAML (YAML wins per
    # ConfigManager.load_merged precedence).
    yaml_ids = {r["provider_id"] for r in rows}
    if gateway.storage is not None:
        for db_row in await gateway.storage.list_managed_providers():
            db_project = db_row.get("project")
            if db_project is None:
                # Legacy global row: belongs to /v1/providers, not here.
                continue
            pid = db_row["provider_id"]
            if pid in yaml_ids:
                continue
            try:
                plaintext = decrypt(db_row.get("api_key_encrypted", ""))
            except ValueError:
                plaintext = ""
            ptype = db_row["provider_type"]
            rows.append(
                {
                    "project": db_project,
                    "provider": ptype,
                    "provider_id": pid,
                    "source": "db",
                    "api_key_masked": mask(plaintext) if plaintext else None,
                    "base_url": db_row.get("base_url"),
                    "type": "local" if ptype in _LOCAL_PROVIDER_NAMES else "cloud",
                }
            )

    if project is not None:
        rows = [r for r in rows if r["project"] == project]

    rows.sort(key=lambda r: (r["project"], r["provider"]))
    return {"providers": rows}
