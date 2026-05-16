"""Dashboard endpoints under /api/projects/{id}/branding.

Three handlers backing the v0.5.0 white-label branding feature
(REQ-VG-ROUTE-004):

- GET  /api/projects/{id}/branding         -> read current branding payload
- POST /api/projects/{id}/branding         -> upsert branding dict (no file)
- POST /api/projects/{id}/branding/logo    -> upload a PNG/SVG logo

The branding directory itself is mounted at ``/static/branding/*`` by
:func:`mount_static_branding` in this module; the daemon's
``ApplicationBuilder`` calls that during ``_configure_routers`` so
uploaded logos resolve at ``http://<daemon>/static/branding/<id>.<ext>``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

from voicegateway.server.api._deps import get_gateway

if TYPE_CHECKING:
    from fastapi import FastAPI

    from voicegateway.core.gateway import Gateway

# OQ4 locks for the v0.5.0 logo upload:
#  - 256 KB on the wire
#  - PNG or SVG only
#  - 512x512 px max (PNG only; SVG is vector so dimension check is skipped)
_BRANDING_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent.parent
    / "dashboard"
    / "api"
    / "static"
    / "branding"
)
_BRANDING_DIR.mkdir(parents=True, exist_ok=True)
_MAX_LOGO_BYTES = 256 * 1024
_MAX_LOGO_DIMENSION = 512
_ALLOWED_LOGO_FORMATS = ("PNG", "SVG")

router = APIRouter(prefix="/projects", tags=["dashboard"])


def mount_static_branding(app: FastAPI) -> None:
    """Mount the branding upload directory at ``/static/branding``.

    Called by the daemon's ``ApplicationBuilder`` so the uploaded logos
    resolve at ``http://<daemon>/static/branding/<project_id>.<ext>``.
    Idempotent: a second call replaces the existing mount.
    """
    app.mount(
        "/static/branding",
        StaticFiles(directory=_BRANDING_DIR),
        name="branding",
    )


@router.get("/{project_id}/branding")
async def get_project_branding(
    project_id: str, gateway: Gateway = Depends(get_gateway)
) -> dict[str, Any]:
    """Return the active white-label branding for a project.

    Returns ``{"project_id": ..., "branding": null}`` when no branding
    is set so the FE can render the default brand (AC-3).
    """
    if gateway.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")
    project = await gateway.storage.get_managed_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found")
    return {"project_id": project_id, "branding": project.get("branding")}


@router.post("/{project_id}/branding")
async def update_project_branding(
    project_id: str,
    body: dict[str, Any] = Body(...),
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Update the project's branding (logo_url / accent_color / product_name).

    Body is the branding dict; storage's ``_validate_branding`` enforces
    shape (hex regex, 64-char product_name cap, no unknown keys).
    Returns the validated branding plus the project_id so the FE can
    re-apply the CSS variables on the next layout mount.
    """
    if gateway.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")
    existing = await gateway.storage.get_managed_project(project_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found")
    try:
        await gateway.storage.upsert_managed_project(
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
    refreshed = await gateway.storage.get_managed_project(project_id)
    return {
        "project_id": project_id,
        "branding": (refreshed or {}).get("branding"),
    }


@router.post("/{project_id}/branding/logo")
async def upload_project_logo(
    project_id: str,
    file: UploadFile = File(...),
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Upload a project's logo image.

    OQ4 constraints (validated by Pillow when the format is PNG):
    - Max 256 KB on the wire.
    - PNG or SVG only.
    - Max 512x512 pixels (PNG only; SVG is vector so dimension check
      is skipped).

    Persists the file under
    ``dashboard/api/static/branding/{project_id}.{ext}`` and stamps the
    returned URL onto the project's ``branding.logo_url``. The endpoint
    does NOT update other branding fields; call
    POST /api/projects/{id}/branding for that.
    """
    if gateway.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")
    existing = await gateway.storage.get_managed_project(project_id)
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
        # validation is out of scope for v0.5.0; the operator should
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

    # Merge with existing branding so we don't clobber accent_color or
    # product_name.
    current_branding = (existing or {}).get("branding") or {}
    new_branding = {**current_branding, "logo_url": logo_url}
    try:
        await gateway.storage.upsert_managed_project(
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
