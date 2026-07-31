"""Dashboard endpoints under /api/projects/{id}/branding.

Three handlers backing the v0.5.0 white-label branding feature
(REQ-VG-ROUTE-004):

- GET  /api/projects/{id}/branding         -> read current branding payload
- POST /api/projects/{id}/branding         -> upsert branding dict (no file)
- POST /api/projects/{id}/branding/logo    -> upload a PNG/SVG logo

The branding directory itself is mounted at ``/static/branding/*`` by
:func:`mount_static_branding` in this module; the daemon's
``ApplicationBuilder`` calls that during ``_configure_routers`` so
uploaded logos resolve at
``http://<daemon>/static/branding/uploads/<id>.<ext>``.

Three properties the logo upload has to hold, because the bytes it takes
are attacker-controlled and are then served same-origin:

1. **It is an admin write.** See :func:`upload_project_logo`.
2. **An uploaded SVG must not be able to run script.** An SVG opened as a
   top-level document is an active document, not an image, so a payload
   under ``/static/branding`` would run in the dashboard's own origin and
   read its ``localStorage`` token. Two independent defences:
   :func:`_reject_unsafe_svg` refuses the content, and
   :class:`_InertStaticFiles` serves the whole directory under a CSP
   sandbox so anything that slips past the first defence is still inert.
3. **The write must stay inside the uploads directory.** Uploads land in
   the ``uploads/`` SUBDIRECTORY, never in the branding root, so a project
   id can never name (and overwrite) one of the assets shipped in the repo
   (``default.svg``, ``logo.svg``, ``favicon.svg``, ...). The filename is
   derived by :func:`_logo_filename` from a strict allow-list, and
   :func:`_resolve_logo_target` re-checks containment on the resolved path.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

from voicegateway.core.auth import ADMIN_SCOPE
from voicegateway.server.api._deps import get_gateway, require_scope

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.responses import Response
    from starlette.types import Scope

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

# Uploaded logos go one level down, into their own subdirectory. The branding
# root holds assets that ship in the repo (default.svg, logo.svg, favicon.svg,
# wordings.png); before this split an upload for the project literally called
# "default" wrote default.svg and replaced a tracked file on disk.
_UPLOAD_SUBDIR = "uploads"

# Served with every branding asset. `sandbox` (no tokens) drops the response
# into an opaque origin and disables script, so a top-level navigation to an
# uploaded SVG cannot touch the dashboard's origin; `default-src 'none'` blocks
# script and subresource loads a second time. Neither breaks <img src=...>,
# which is how the dashboard renders the logo. `style-src 'unsafe-inline'`
# keeps inline presentation attributes working for an ordinary vector logo.
_BRANDING_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"

router = APIRouter(prefix="/projects", tags=["dashboard"])


class _InertStaticFiles(StaticFiles):
    """StaticFiles that serves branding assets as inert documents.

    The upload directory is the one place on the daemon where a caller can
    put bytes that are later served from the dashboard's own origin, so the
    response carries its own defence rather than trusting the content check
    upstream of it. ``get_response`` is the documented override point and is
    also on the 404 path, where the headers simply do not apply.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers.setdefault("Content-Security-Policy", _BRANDING_CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response


def mount_static_branding(app: FastAPI) -> None:
    """Mount the branding upload directory at ``/static/branding``.

    Called by the daemon's ``ApplicationBuilder`` so the uploaded logos
    resolve at ``http://<daemon>/static/branding/uploads/<project_id>.<ext>``.
    Idempotent: a second call replaces the existing mount.
    """
    app.mount(
        "/static/branding",
        _InertStaticFiles(directory=_BRANDING_DIR),
        name="branding",
    )


# ---------------------------------------------------------------------------
# Where an upload is allowed to land
# ---------------------------------------------------------------------------

# The one shape a project id may contribute to a filename verbatim: ASCII
# alphanumerics, underscore and dash, starting with an alphanumeric, at most
# 64 chars. No dot, no slash, no backslash, no NUL, so "..", "../x" and
# "x/../../y" all fail it and take the digest branch below.
_SAFE_STEM_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


def _upload_dir() -> Path:
    """Directory uploaded logos are written to.

    Derived at call time rather than bound at import so a test can redirect
    ``_BRANDING_DIR`` to a tmp_path and have BOTH the writes and the static
    mount follow it, instead of writing into the repo's tracked asset tree.
    """
    return _BRANDING_DIR / _UPLOAD_SUBDIR


def _logo_filename(project_id: str, suffix: str) -> str:
    """Return the on-disk filename for a project's logo, safe by construction.

    A project id is an arbitrary caller-supplied string (nothing validates it
    at creation), so it is never interpolated into a path unchecked. An id
    that matches :data:`_SAFE_STEM_RE` is used as-is, which keeps the URL
    readable for the ordinary ``acme``-style id; anything else is replaced
    wholesale by a digest of the id, so the result is still deterministic (a
    re-upload replaces that project's own logo) but cannot contain a path
    separator, a traversal segment or a NUL.
    """
    stem = project_id if _SAFE_STEM_RE.match(project_id) else None
    if stem is None:
        digest = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:16]
        stem = f"p-{digest}"
    return f"{stem}.{suffix}"


def _resolve_logo_target(project_id: str, suffix: str) -> Path:
    """Absolute write path for a logo, re-checked to be inside the uploads dir.

    :func:`_logo_filename` already guarantees this; the check is kept because
    the cost of being wrong here is an arbitrary file write, and a future edit
    to the filename rule should fail closed rather than escape.
    """
    root = _upload_dir().resolve()
    filename = _logo_filename(project_id, suffix)
    target = (root / filename).resolve()
    if target.parent != root or target.name != filename:
        raise HTTPException(
            status_code=400,
            detail="logo filename resolves outside the branding upload directory",
        )
    return target


# ---------------------------------------------------------------------------
# What an uploaded SVG is allowed to contain
# ---------------------------------------------------------------------------

# Elements that either run script or can retarget an attribute to something
# that does. A logo needs none of them.
_SVG_FORBIDDEN_TAGS = frozenset(
    {
        "script",
        "foreignobject",
        "iframe",
        "embed",
        "object",
        "handler",
        "audio",
        "video",
        "animate",
        "set",
    }
)

# URL schemes refused in any attribute value. `javascript:` executes; `data:`
# is refused because a data: document inherits nothing to stop it (an operator
# who needs a raster logo can upload the PNG itself).
_SVG_FORBIDDEN_SCHEMES = ("javascript:", "data:", "vbscript:")

# Markers refused on the raw bytes as well as on the parsed tree, so they are
# caught even where they never become a node: entity declarations (billion
# laughs / smuggled markup) and a script element hidden by malformed markup
# that expat drops but a browser's lenient parser would still honour.
_SVG_FORBIDDEN_RAW = ("<script", "<foreignobject", "<!entity", "javascript:")

_WHITESPACE_RE = re.compile(r"[\s\x00-\x1f]+")


def _local_name(tag: str) -> str:
    """Strip the ``{namespace}`` prefix ElementTree puts on qualified names."""
    return tag.rsplit("}", 1)[-1].lower()


def _reject_unsafe_svg(content: bytes) -> None:
    """Raise 400 unless ``content`` is an SVG that cannot execute anything.

    The pre-hardening check was ``b"<svg" in content[:512]``, which accepts a
    file whose very next element is ``<script>``. Two passes now:

    - A raw pass over the decoded text, whitespace-stripped so ``< script``
      and ``java script:`` normalise onto the marker, for the handful of
      things that must not appear anywhere in the file. Stripping the
      ``\\x00-\\x1f`` range also collapses UTF-16-encoded ASCII (``<\\x00s``
      ...) onto the same markers, so a re-encoded payload does not slip the
      pass.
    - A parse pass over the element tree, which is encoding-independent (a
      UTF-16 payload defeats a byte scan but not the parser) and precise:
      forbidden elements, any ``on*`` event-handler attribute, and any
      attribute value carrying an executable scheme.

    Refusing rather than rewriting is deliberate: a sanitiser that silently
    edits the operator's file has to be right about every browser quirk,
    whereas a refusal is a decision the operator can see and act on.
    """
    text = content.decode("utf-8", errors="replace").lower()
    flat = _WHITESPACE_RE.sub("", text)
    for marker in _SVG_FORBIDDEN_RAW:
        if marker in flat:
            raise HTTPException(
                status_code=400,
                detail=f"SVG rejected: contains {marker!r}",
            )

    # Stdlib ElementTree rather than defusedxml, which is not a dependency of
    # this package: the two attacks it guards are already closed here. Entity
    # declarations are refused by the raw pass above (so no billion laughs),
    # and expat under ElementTree never resolves an external entity - an
    # undefined reference is a ParseError, i.e. a 400 below, not a fetch.
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"SVG rejected: not well-formed XML ({exc})",
        ) from exc
    if _local_name(root.tag) != "svg":
        raise HTTPException(
            status_code=400,
            detail=f"SVG rejected: root element is {_local_name(root.tag)!r}, not 'svg'",
        )

    for element in root.iter():
        name = _local_name(str(element.tag))
        if name in _SVG_FORBIDDEN_TAGS:
            raise HTTPException(
                status_code=400,
                detail=f"SVG rejected: forbidden element <{name}>",
            )
        for attr, value in element.attrib.items():
            attr_name = _local_name(attr)
            if attr_name.startswith("on"):
                raise HTTPException(
                    status_code=400,
                    detail=f"SVG rejected: event-handler attribute {attr_name!r}",
                )
            flat_value = _WHITESPACE_RE.sub("", value).lower()
            for scheme in _SVG_FORBIDDEN_SCHEMES:
                if scheme in flat_value:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"SVG rejected: {scheme} URL in attribute {attr_name!r}"
                        ),
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


@router.post(
    "/{project_id}/branding/logo",
    dependencies=[Depends(require_scope(ADMIN_SCOPE))],
)
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
    ``dashboard/api/static/branding/uploads/{stem}.{ext}`` and stamps the
    returned URL onto the project's ``branding.logo_url``. The endpoint
    does NOT update other branding fields; call
    POST /api/projects/{id}/branding for that.

    **Auth.** This route carried no dependency at all: anyone who could
    reach the port could write bytes into a directory the dashboard serves
    from its own origin. It takes ``require_scope(ADMIN_SCOPE)``, the same
    gate the API-key routers take, because white-label branding is an
    operator/agency setting rather than a read. The dependency is declared
    on the ROUTE and not on the router (the S1 idiom) for the reason
    ``dashboard/replay.py`` gives: this router also carries
    ``GET /{id}/branding``, which every dashboard layout mount calls to
    apply the brand, and gating that read behind the admin scope would
    log out a read-scoped operator on page load. The gate is a no-op while
    no API keys are configured, so the self-hosted default is unchanged.
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
        # An SVG is an active document, not an image, so "looks like SVG" is
        # not a safety property. _reject_unsafe_svg is what stands between an
        # upload and script running on the dashboard's origin.
        if b"<svg" not in content[:512].lower():
            raise HTTPException(status_code=400, detail="file does not look like SVG")
        _reject_unsafe_svg(content)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported logo format: must be PNG or SVG (got "
            f"content_type={content_type!r})",
        )

    # Never `_BRANDING_DIR / f"{project_id}.{suffix}"`: that interpolates a
    # caller-supplied id straight into a path, and it put the write in the
    # same directory as the repo's own default.svg / logo.svg / favicon.svg.
    target = _resolve_logo_target(project_id, suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    logo_url = f"/static/branding/{_UPLOAD_SUBDIR}/{target.name}"

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
