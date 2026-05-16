"""FastAPI dashboard API for VoiceGateway."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
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

_BRANDING_DIR = Path(__file__).parent / "static" / "branding"
_BRANDING_DIR.mkdir(parents=True, exist_ok=True)
_MAX_LOGO_BYTES = 256 * 1024  # OQ4 lock: 256 KB.
_MAX_LOGO_DIMENSION = 512  # OQ4 lock: 512x512 px.
_ALLOWED_LOGO_FORMATS = ("PNG", "SVG")  # OQ4 lock: PNG or SVG.

# Mount the branding directory at /static/branding so uploaded logos
# resolve at https://<dashboard>/static/branding/<project_id>.<ext>.
app.mount(
    "/static/branding",
    StaticFiles(directory=_BRANDING_DIR),
    name="branding",
)


@app.get("/api/projects/{project_id}/branding")
async def get_project_branding(project_id: str) -> dict[str, Any]:
    """Return the active white-label branding for a project.

    Returns ``{"project_id": ..., "branding": null}`` when no
    branding is set so the FE can render the default brand
    (AC-3).
    """
    gw = _get_gateway()
    if gw.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")
    project = await gw.storage.get_managed_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found")
    return {"project_id": project_id, "branding": project.get("branding")}


@app.post("/api/projects/{project_id}/branding")
async def update_project_branding(
    project_id: str, body: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    """Update the project's branding (logo_url / accent_color / product_name).

    Body is the branding dict; storage's ``_validate_branding``
    enforces shape (hex regex, 64-char product_name cap, no unknown
    keys). Returns the validated branding plus the project_id so the
    FE can re-apply the CSS variables on the next layout mount.
    """
    gw = _get_gateway()
    if gw.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")
    existing = await gw.storage.get_managed_project(project_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found")
    try:
        await gw.storage.upsert_managed_project(
            project_id=project_id,
            name=existing.get("name", project_id),
            description=existing.get("description", ""),
            daily_budget=existing.get("daily_budget", 0.0),
            budget_action=existing.get("budget_action", "warn"),
            default_stack=existing.get("default_stack"),
            stt_model=existing.get("stt_model"),
            llm_model=existing.get("llm_model"),
            tts_model=existing.get("tts_model"),
            tags=existing.get("tags"),
            branding=body,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    refreshed = await gw.storage.get_managed_project(project_id)
    return {
        "project_id": project_id,
        "branding": (refreshed or {}).get("branding"),
    }


@app.post("/api/projects/{project_id}/branding/logo")
async def upload_project_logo(
    project_id: str, file: UploadFile = File(...)
) -> dict[str, Any]:
    """Upload a project's logo image.

    OQ4 constraints (validated by Pillow when the format is PNG):
    - Max 256 KB on the wire.
    - PNG or SVG only.
    - Max 512x512 pixels (PNG only; SVG is vector so dimension
      check is skipped).

    Persists the file under
    ``dashboard/api/static/branding/{project_id}.{ext}`` and stamps
    the returned URL onto the project's ``branding.logo_url``. The
    endpoint does NOT update other branding fields; call POST
    /api/projects/{id}/branding for that.
    """
    gw = _get_gateway()
    if gw.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")
    existing = await gw.storage.get_managed_project(project_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found")

    content = await file.read()
    if len(content) > _MAX_LOGO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"logo exceeds {_MAX_LOGO_BYTES // 1024} KB cap "
                f"(got {len(content)} bytes)"
            ),
        )

    content_type = (file.content_type or "").lower()
    suffix: str
    if content_type == "image/png" or (file.filename or "").lower().endswith(".png"):
        suffix = "png"
        try:
            from io import BytesIO

            from PIL import Image, UnidentifiedImageError
        except ImportError as exc:
            raise HTTPException(
                status_code=503,
                detail="Pillow not installed; install voicegateway[dashboard]",
            ) from exc
        try:
            img = Image.open(BytesIO(content))
            img.verify()
            # Re-open after verify (PIL invalidates the handle).
            img = Image.open(BytesIO(content))
        except (UnidentifiedImageError, OSError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid PNG: {exc}") from exc
        if img.format != "PNG":
            raise HTTPException(
                status_code=400,
                detail=f"expected PNG, got {img.format}",
            )
        if img.width > _MAX_LOGO_DIMENSION or img.height > _MAX_LOGO_DIMENSION:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"logo dimensions {img.width}x{img.height} exceed "
                    f"{_MAX_LOGO_DIMENSION}x{_MAX_LOGO_DIMENSION} cap"
                ),
            )
    elif content_type == "image/svg+xml" or (file.filename or "").lower().endswith(
        ".svg"
    ):
        suffix = "svg"
        # SVG is text/XML; reject obvious binary garbage. Full XML
        # validation is out of scope for v0.5.0 — operator should
        # control which agency staff can upload.
        if b"<svg" not in content[:512].lower():
            raise HTTPException(status_code=400, detail="file does not look like SVG")
    else:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported logo format: must be PNG or SVG (got "
            f"content_type={content_type!r})",
        )

    target = _BRANDING_DIR / f"{project_id}.{suffix}"
    target.write_bytes(content)
    logo_url = f"/static/branding/{project_id}.{suffix}"

    # Merge with existing branding so we don't clobber accent_color
    # or product_name.
    current_branding = (existing or {}).get("branding") or {}
    new_branding = {**current_branding, "logo_url": logo_url}
    try:
        await gw.storage.upsert_managed_project(
            project_id=project_id,
            name=existing.get("name", project_id),
            description=existing.get("description", ""),
            daily_budget=existing.get("daily_budget", 0.0),
            budget_action=existing.get("budget_action", "warn"),
            default_stack=existing.get("default_stack"),
            stt_model=existing.get("stt_model"),
            llm_model=existing.get("llm_model"),
            tts_model=existing.get("tts_model"),
            tags=existing.get("tags"),
            branding=new_branding,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "project_id": project_id,
        "logo_url": logo_url,
        "bytes": len(content),
        "format": suffix.upper(),
    }


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
