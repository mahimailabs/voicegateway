"""FastAPI dashboard API for VoiceGateway."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from voicegateway.core.auth import resolve_cors_origins
from voicegateway.schemas.guardrail_policy_schema import (
    ACTIVE_GUARDRAIL_ACTIONS,
    GUARDRAIL_CATEGORIES,
)

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

logger = logging.getLogger(__name__)

# Local providers don't need an api_key to be considered "configured" —
# they run against a local model server (ollama) or bundled binaries
# (whisper / kokoro / piper). Used by /api/status and
# /api/providers/by-project to decide between cloud and local typing.
_LOCAL_PROVIDER_NAMES = frozenset({"ollama", "whisper", "kokoro", "piper"})

app = FastAPI(
    title="VoiceGateway Dashboard",
    version="0.6.0",
)

# Set by the CLI / combined server when starting the dashboard.
_gateway: Any = None
# Tracks whether CORS has been configured so configure() is idempotent.
_cors_configured = False


def _get_gateway() -> Gateway:
    if _gateway is None:
        raise RuntimeError("Gateway not initialized. Start via: voicegw dashboard")
    return _gateway  # type: ignore[no-any-return]


def _guardrail_since(days: int) -> str:
    days = max(1, min(days, 365))
    return (datetime.now(tz=UTC) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _validate_guardrail_event_filter(
    *, category: str | None, action: str | None
) -> None:
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


# ---------------------------------------------------------------------------
# v0.5.0 cross-modality routing + white-label branding endpoints.
# REQ-VG-ROUTE-003 (Routing observations view) + REQ-VG-ROUTE-004
# (per-project branding upload).
# ---------------------------------------------------------------------------

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
            "fix": "Run: cd src/dashboard/frontend && npm install && npm run build",
        }
