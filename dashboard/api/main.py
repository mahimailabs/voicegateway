"""FastAPI dashboard API for VoiceGateway."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from voicegateway.core.auth import load_api_keys, resolve_cors_origins

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

logger = logging.getLogger(__name__)

app = FastAPI(
    title="VoiceGateway Dashboard",
    version="0.1.0",
)

# Set by the CLI / combined server when starting the dashboard.
_gateway: Any = None
# Tracks whether CORS has been configured so configure() is idempotent.
_cors_configured = False


def _get_gateway() -> Gateway:
    if _gateway is None:
        raise RuntimeError("Gateway not initialized. Start via: voicegw dashboard")
    return _gateway  # type: ignore[no-any-return]


def configure(gateway: Gateway) -> None:
    """Attach a Gateway and configure CORS from its auth settings.

    Called by the CLI before starting uvicorn. The combined server does
    not call this — it mounts the dashboard routes onto its own app,
    which already has CORS configured by voicegateway.server.build_app.
    """
    global _gateway, _cors_configured  # noqa: PLW0603
    _gateway = gateway
    if _cors_configured:
        return
    origins = resolve_cors_origins(gateway.config.auth)
    if origins == ["*"]:
        logger.warning(
            "Dashboard CORS: allow_origins=['*']. Set auth.cors_origins "
            "in voicegw.yaml to restrict."
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _cors_configured = True


@app.get("/api/auth-status")
async def get_auth_status() -> dict:
    """Report whether the gateway requires a token for mutating endpoints.

    Used by the frontend to decide whether to show the login gate. The
    response never exposes key material.
    """
    gw = _get_gateway()
    keys = load_api_keys(gw.config.auth)
    return {"auth_required": bool(keys)}


@app.get("/api/status")
async def get_status() -> dict:
    """Get status of all configured providers and models."""
    gw = _get_gateway()
    config = gw.config

    providers = {}
    for name, cfg in config.providers.items():
        has_key = bool(cfg.get("api_key")) or name in ("ollama", "whisper", "kokoro", "piper")
        providers[name] = {
            "configured": has_key,
            "type": "local" if name in ("ollama", "whisper", "kokoro", "piper") else "cloud",
        }

    models = {}
    for modality, modality_models in config.models.items():
        if isinstance(modality_models, dict):
            for model_id, model_cfg in modality_models.items():
                if isinstance(model_cfg, dict):
                    models[model_id] = {
                        "modality": modality,
                        "provider": model_cfg.get("provider", ""),
                    }

    return {
        "providers": providers,
        "models": models,
        "fallbacks": config.fallbacks,
    }


@app.get("/api/costs")
async def get_costs(
    period: str = Query("today", enum=["today", "week", "month", "all"]),
    project: str | None = Query(None),
) -> dict:
    """Get cost summary for a period, optionally filtered by project."""
    gw = _get_gateway()
    if gw.storage is None:
        return {"period": period, "project": project, "total": 0.0, "by_provider": {}, "by_model": {}, "by_project": {}}
    summary = await gw.storage.get_cost_summary(period, project=project)
    if project is None:
        summary["by_project"] = await gw.storage.get_cost_by_project(period)
    else:
        summary["by_project"] = {}
    return summary


@app.get("/api/latency")
async def get_latency(
    period: str = Query("today", enum=["today", "week"]),
    project: str | None = Query(None),
) -> dict:
    """Get latency statistics, optionally filtered by project."""
    gw = _get_gateway()
    if gw.storage is None:
        return {}
    pcts = gw.config.latency.get("percentiles") or [50.0, 95.0, 99.0]
    return await gw.storage.get_latency_stats(
        period, project=project, percentiles=pcts
    )


@app.get("/api/logs")
async def get_logs(
    limit: int = Query(100, ge=1, le=1000),
    modality: str | None = Query(None, enum=["stt", "llm", "tts"]),
    project: str | None = Query(None),
) -> list[dict]:
    """Get recent request logs, optionally filtered by modality and/or project."""
    gw = _get_gateway()
    if gw.storage is None:
        return []
    return await gw.storage.get_recent_requests(limit=limit, modality=modality, project=project)


@app.get("/api/overview")
async def get_overview(
    project: str | None = Query(None),
) -> dict:
    """Get dashboard overview stats, optionally filtered by project."""
    gw = _get_gateway()
    config = gw.config

    model_count = 0
    for modality_models in config.models.values():
        if isinstance(modality_models, dict):
            model_count += len(modality_models)

    if gw.storage is None:
        return {
            "total_requests": 0,
            "total_cost": 0.0,
            "active_models": model_count,
            "providers_configured": len(config.providers),
        }

    cost_summary = await gw.storage.get_cost_summary("today", project=project)
    total_all = await gw.storage.get_cost_summary("all", project=project)

    return {
        "total_requests": sum(
            d["requests"] for d in total_all.get("by_provider", {}).values()
        ),
        "total_cost_today": cost_summary.get("total", 0.0),
        "total_cost_all": total_all.get("total", 0.0),
        "active_models": model_count,
        "providers_configured": len(config.providers),
    }


@app.get("/api/projects")
async def get_projects() -> dict:
    """List configured projects with today's stats."""
    gw = _get_gateway()
    projects = gw.list_projects()
    stats = {}
    if gw.storage is not None:
        for p in projects:
            stats[p["id"]] = await gw.storage.get_project_stats(p["id"])
    return {"projects": projects, "stats": stats}


# ---------------------------------------------------------------------------
# Serve the Vite-built frontend (if it exists)
# ---------------------------------------------------------------------------

_FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "dist"

if _FRONTEND_DIR.exists() and (_FRONTEND_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIR / "assets"), name="assets")

    @app.get("/")
    async def serve_index():
        return FileResponse(_FRONTEND_DIR / "index.html")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        """SPA fallback — React Router handles client-side routing."""
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        file_path = _FRONTEND_DIR / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_FRONTEND_DIR / "index.html")

else:
    @app.get("/")
    async def missing_frontend():
        return {
            "error": "Frontend not built",
            "fix": "Run: cd dashboard/frontend && npm install && npm run build",
        }
