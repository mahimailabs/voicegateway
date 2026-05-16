"""FastAPI dashboard API for VoiceGateway."""

from __future__ import annotations

import dataclasses
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from voicegateway.core.auth import resolve_cors_origins
from voicegateway.repository import (
    guardrail_events_repository as guardrail_events,
)
from voicegateway.repository import (
    latency_observations_repository as latency_observations,
)
from voicegateway.repository import (
    replay_repository as replay,
)
from voicegateway.repository import (
    tenants_repository as tenants,
)
from voicegateway.repository import (
    virtual_keys_repository as virtual_keys,
)
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


@app.get("/api/guardrails/events")
async def get_guardrail_events(
    days: int = Query(7, ge=1, le=365),
    project: str | None = Query(None),
    tenant: str | None = Query(None),
    category: str | None = Query(None),
    action: str | None = Query(None),
    event_type: str | None = Query(None, pattern="^(fired|bypassed)$"),
    limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    """Return filtered guardrail audit events for the dashboard."""
    gw = _get_gateway()
    if gw.storage is None:
        return {"events": [], "filter": {"project": project, "tenant": tenant}}
    _validate_guardrail_event_filter(category=category, action=action)
    await gw.storage._ensure_initialized()
    async with gw.storage._conn.session() as db:
        rows = await guardrail_events.list_events(
            db,
            since=_guardrail_since(days),
            project=project,
            tenant=tenant,
            category=category,
            action=action,
            event_type=event_type,
            limit=limit,
        )
    return {
        "events": [dataclasses.asdict(row) for row in rows],
        "filter": {
            "days": days,
            "project": project,
            "tenant": tenant,
            "category": category,
            "action": action,
            "event_type": event_type,
        },
    }


@app.get("/api/guardrails/aggregate")
async def get_guardrail_aggregate(
    days: int = Query(7, ge=1, le=365),
    project: str | None = Query(None),
    tenant: str | None = Query(None),
    category: str | None = Query(None),
) -> dict[str, Any]:
    """Return guardrail counts plus optional category drilldown."""
    gw = _get_gateway()
    if gw.storage is None:
        return {"counts": [], "top_sessions": []}
    _validate_guardrail_event_filter(category=category, action=None)
    await gw.storage._ensure_initialized()
    async with gw.storage._conn.session() as db:
        since = _guardrail_since(days)
        counts = await guardrail_events.aggregate_counts(
            db, since=since, project=project, tenant=tenant
        )
        top_sessions = (
            await guardrail_events.top_sessions_by_category(
                db,
                category=category,
                since=since,
                project=project,
                tenant=tenant,
            )
            if category
            else []
        )
    return {
        "counts": [dataclasses.asdict(row) for row in counts],
        "top_sessions": [dataclasses.asdict(row) for row in top_sessions],
        "filter": {
            "days": days,
            "project": project,
            "tenant": tenant,
            "category": category,
        },
    }


# ----------------------------------------------------------------------
# v0.3.0 conversation replay (REQ-VG-REPLAY-001..006).
#
# Four handlers backing the dashboard Replay page (T11) and the
# storage-usage view. Same `/api/*` prefix as the v0.2.0 metrics
# endpoints, deviating from the Foundry's literal `/v1/*` prefix per
# the dashboard convention; the main server may publish `/v1/*` mirrors
# later if SDK consumers need them.
# ----------------------------------------------------------------------


@app.get("/api/sessions/{session_id}/replay")
async def get_session_replay(session_id: str) -> dict[str, Any]:
    """Return the full time-ordered replay for one session.

    Used by the Replay page (T11) to pre-fetch the full timeline on
    page load (OQ3 resolution: pre-fetch over streaming). Each event
    carries its modality so the consumer routes to the right pane.

    Empty list when the session has no captured replay events
    (pre-v0.3.0 sessions or projects with ``replay.enabled: false``);
    the FE renders a PreV030Banner in that case (REQ-VG-REPLAY-001
    AC-3).
    """
    gw = _get_gateway()
    if gw.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")
    await gw.storage._ensure_initialized()
    async with gw.storage._conn.session() as db:
        events = await replay.read_full_replay(db, session_id)
    return {
        "session_id": session_id,
        "events": [dataclasses.asdict(e) for e in events],
    }


@app.delete("/api/sessions/{session_id}/replay")
async def delete_session_replay(session_id: str) -> dict[str, Any]:
    """Delete every replay row tied to a session (REQ-VG-REPLAY-006 AC-3).

    Cascade across all four ``replay_*`` tables in one transaction.
    Returns the total row count deleted (across modalities) so the
    caller can confirm the cleanup landed.
    """
    gw = _get_gateway()
    if gw.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")
    await gw.storage._ensure_initialized()
    async with gw.storage._conn.session() as db:
        deleted = await replay.delete_replay(db, session_id)
    return {"session_id": session_id, "deleted_rows": deleted}


@app.get("/api/replay/storage")
async def get_replay_storage() -> dict[str, Any]:
    """Return per-project replay storage breakdown.

    Sums ``sessions.replay_size_bytes`` per project (the column is
    populated by T08's ``finalize_session_replay``). Sessions without
    captured replay (NULL replay_size_bytes) contribute 0 to the sum.
    The Refinery requires the dashboard surface this so "the cost is
    not invisible" to the developer.
    """
    gw = _get_gateway()
    if gw.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")
    await gw.storage._ensure_initialized()
    async with gw.storage._conn.session() as db:
        result = await db.execute(
            text(
                "SELECT project, COALESCE(SUM(replay_size_bytes), 0) "
                "FROM sessions "
                "WHERE replay_size_bytes IS NOT NULL "
                "GROUP BY project "
                "ORDER BY project ASC"
            )
        )
        per_project = []
        total = 0
        for row in result:
            project, size = row
            size_int = int(size or 0)
            per_project.append(
                {
                    "project": project,
                    "replay_size_bytes": size_int,
                }
            )
            total += size_int
        return {
            "total_replay_size_bytes": total,
            "by_project": per_project,
        }


@app.post("/api/projects/{project_id}/replay/retention")
async def update_replay_retention(
    project_id: str,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Update the retention window for one project.

    Body shape: ``{"retention_days": N}`` (1..365 inclusive). The
    update is applied in-memory to ``gw.config.projects[project_id]
    .replay.retention_days`` so the retention worker (T06) picks it
    up on its next tick.

    v0.3.0 limitation: the update is not persisted to ``voicegw.yaml``
    on disk. Restart re-reads the original value. Persistence is a
    follow-up that needs config-file-write infrastructure; the in-
    memory mutation matches the runtime contract the retention worker
    reads from.
    """
    gw = _get_gateway()
    new_days_raw = body.get("retention_days")
    if not isinstance(new_days_raw, int) or new_days_raw < 1 or new_days_raw > 365:
        raise HTTPException(
            status_code=422,
            detail="retention_days must be an int in [1, 365]",
        )
    project = gw.config.projects.get(project_id) if gw.config else None
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    project.replay.retention_days = new_days_raw
    return {
        "project_id": project_id,
        "retention_days": new_days_raw,
    }


@app.get("/api/providers/by-project")
async def get_providers_by_project(project: str | None = Query(None)) -> dict:
    """Return per-project provider keys (v0.0.5).

    Surfaces both YAML-defined ``projects.<id>.providers.<name>`` blocks
    and DB-managed managed_providers rows scoped to a project. The
    api_key is masked. When ``project=...`` is supplied, only rows
    scoped to that project are returned.
    """
    from voicegateway.core.crypto import decrypt, mask

    gw = _get_gateway()
    rows: list[dict[str, Any]] = []

    # 1. YAML projects.<id>.providers.<name>. After Item 1 fixed the
    # config_manager merge, ``config.projects[<id>].providers`` can
    # contain DB-managed rows alongside YAML entries; skip the DB ones
    # here so the loop below tags them with source="db" instead.
    for proj_id, proj_cfg in gw.config.projects.items():
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
    if gw.storage is not None:
        for db_row in await gw.storage.list_managed_providers():
            db_project = db_row.get("project")
            if db_project is None:
                # Legacy global row — belongs to /v1/providers, not here.
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


# ---------------------------------------------------------------------------
# v0.4.0 multi-tenant API surface (REQ-VG-TENANT-002 + REQ-VG-TENANT-003).
#
# Tenants are derived from DISTINCT sessions.tenant_id; the dashboard's
# filter feed and per-tenant overview are read-only here. Virtual keys
# expose plaintext exactly once at creation (the "show key once" modal
# on the FE owns persistence to the operator's clipboard) and support
# soft revocation per OQ5.
# ---------------------------------------------------------------------------


@app.get("/api/tenants")
async def list_tenants_endpoint(
    limit: int = Query(50, ge=1, le=1000),
    q: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    """Return the tenant index for the dashboard filter typeahead.

    ``q`` is a substring match against tenant_id (``%`` and ``_``
    escaped to literal characters). The implicit unattributed bucket
    (NULL tenant_id) is included as a separate ``unattributed`` field
    so the FE can render it as a muted pill below the tenant list.
    """
    gw = _get_gateway()
    if gw.storage is None:
        return {
            "tenants": [],
            "unattributed": {
                "session_count": 0,
                "total_cost_usd": 0.0,
                "first_seen": None,
                "last_seen": None,
            },
        }
    await gw.storage._ensure_initialized()
    async with gw.storage._conn.session() as db:
        rows = await tenants.list_tenants(db, limit=limit, query=q)
        u = await tenants.get_unattributed_aggregates(db)
    return {
        "tenants": [dataclasses.asdict(r) for r in rows],
        "unattributed": dataclasses.asdict(u),
    }


@app.get("/api/tenants/{tenant_id}")
async def get_tenant_endpoint(tenant_id: str) -> dict[str, Any]:
    """Return aggregates for a single tenant. 404 when unseen."""
    gw = _get_gateway()
    if gw.storage is None:
        raise HTTPException(status_code=404, detail=f"Tenant {tenant_id!r} not found")
    await gw.storage._ensure_initialized()
    async with gw.storage._conn.session() as db:
        row = await tenants.get_tenant(db, tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Tenant {tenant_id!r} not found")
    return dataclasses.asdict(row)


@app.get("/api/virtual_keys")
async def list_virtual_keys_endpoint(
    include_revoked: bool = Query(True),
) -> dict[str, Any]:
    """Return all virtual keys. The plaintext key never appears here.

    Each row carries ``key_prefix`` (the 8-char visible prefix) but
    not the bcrypt hash. ``include_revoked=False`` filters out soft-
    revoked entries.
    """
    gw = _get_gateway()
    if gw.storage is None:
        return {"keys": []}
    await gw.storage._ensure_initialized()
    async with gw.storage._conn.session() as db:
        rows = await virtual_keys.list_keys(db, include_revoked=include_revoked)
    return {"keys": [dataclasses.asdict(r) for r in rows]}


@app.post("/api/virtual_keys")
async def create_virtual_key_endpoint(
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Issue a new virtual key. Returns the plaintext EXACTLY ONCE.

    Body fields:
    - ``name`` (required): human-readable label shown in the dashboard.
    - ``tenant_id`` (optional): if set, requests bearing this key
      auto-tag the session with this tenant (REQ-VG-TENANT-004).
    - ``issued_by`` (optional): free-form audit string.

    The returned ``plaintext`` is the only place the full key appears.
    The FE shows the "save this key" modal and discards it; subsequent
    list responses expose only ``key_prefix``.
    """
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="`name` is required")
    tenant_id_raw = body.get("tenant_id")
    tenant_id = str(tenant_id_raw).strip() if tenant_id_raw not in (None, "") else None
    issued_by_raw = body.get("issued_by")
    issued_by = str(issued_by_raw).strip() if issued_by_raw not in (None, "") else None
    gw = _get_gateway()
    if gw.storage is None:
        raise HTTPException(status_code=503, detail="Storage backend not configured")
    await gw.storage._ensure_initialized()
    async with gw.storage._conn.session() as db:
        created = await virtual_keys.create_virtual_key(
            db, name=name, tenant_id=tenant_id, issued_by=issued_by
        )
    return {
        "id": created.id,
        "plaintext": created.plaintext,
        "row": dataclasses.asdict(created.row),
    }


@app.post("/api/virtual_keys/{key_id}/revoke")
async def revoke_virtual_key_endpoint(key_id: int) -> dict[str, Any]:
    """Soft-revoke a virtual key (OQ5: keeps the row for audit)."""
    gw = _get_gateway()
    if gw.storage is None:
        raise HTTPException(status_code=503, detail="Storage backend not configured")
    await gw.storage._ensure_initialized()
    async with gw.storage._conn.session() as db:
        ok = await virtual_keys.revoke(db, key_id)
        if not ok:
            raise HTTPException(
                status_code=404,
                detail=f"Virtual key {key_id} not found or already revoked",
            )
        row = await virtual_keys.get_by_id(db, key_id)
    if row is None:
        # Should never happen: revoke() just returned True. Defensive.
        raise HTTPException(status_code=404, detail=f"Virtual key {key_id} vanished")
    return {"id": key_id, "revoked": True, "row": dataclasses.asdict(row)}


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


@app.get("/api/routing/observations")
async def get_routing_observations(
    project: str | None = Query(None),
) -> dict[str, Any]:
    """Per-project provider-latency snapshot powering the Routing dashboard view.

    Implements REQ-VG-ROUTE-003 AC-3 (per-project observed-latency,
    refreshed at least hourly — the worker rolls up every 15 minutes
    per OQ2). Each entry carries the provider id, modality, p50/p95,
    sample_count, and the rolling window's start/end timestamps.

    Pass ``project`` to scope to one project; omit for every
    project's observations.
    """

    gw = _get_gateway()
    if gw.storage is None:
        return {"observations": [], "filter": {"project": project}}
    await gw.storage._ensure_initialized()
    async with gw.storage._conn.session() as db:
        rows = (
            await latency_observations.get_for_project(db, project)
            if project
            else await latency_observations.read_all(db)
        )
    return {
        "observations": [dataclasses.asdict(r) for r in rows],
        "filter": {"project": project},
    }


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
