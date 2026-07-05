"""OpenOrca dashboard endpoints: runtime info, snapshot, SSE stream, resolves.

This router backs the OpenOrca fleet UI. It exposes a point-in-time
``/snapshot`` (the roster mapped to the dashboard shape), a live ``/events``
Server-Sent-Events stream, a ``/runtime-info`` capability probe, and an
intervention ``/resolve`` write. Snapshot and stream reads resolve the tenant
from the authenticated principal exactly like the other dashboard reads.

The ``/events`` generator folds a periodic fleet refresh into its own keepalive:
every 15s of idle it emits a ``runtime.status`` heartbeat AND re-reads the
roster to emit a ``fleet.updated`` frame, so offline TTL transitions propagate
to connected clients without an app-lifespan ticker or a client reload.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from voicegateway.repository import (
    guardrail_events_repository,
    session_repository,
    workers_repository,
)
from voicegateway.repository.workers_repository import DEFAULT_TTL_SECONDS
from voicegateway.server.api._deps import (
    Depends,
    Principal,
    get_gateway,
    require_principal,
    resolve_read_tenant,
)
from voicegateway.server.api.openorca.bus import EventBus
from voicegateway.server.api.openorca.mapper import build_snapshot

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(prefix="/openorca", tags=["openorca"])

# Module-level bus shared by the SSE stream and by producers (the heartbeat
# endpoint imports this lazily to publish agent/fleet updates).
bus = EventBus()

_KEEPALIVE_SECONDS = 15.0

# In-memory intervention resolutions, keyed by tenant then interventionId. A
# resolved intervention is filtered out of subsequent snapshots so it stops
# reappearing. In-memory (not durable across restarts, not shared across
# workers): adequate for the console; a persistent store can replace this later.
_RESOLUTIONS: dict[str | None, dict[str, float]] = {}
_RESOLUTION_TTL_SECONDS = 24 * 3600
_RESOLVE_ACTIONS = {"approve", "deny", "later"}


def _mark_resolved(tenant_id: str | None, intervention_id: str) -> None:
    """Record an intervention as resolved for a tenant, pruning stale entries."""
    now = time.time()
    bucket = _RESOLUTIONS.setdefault(tenant_id, {})
    bucket[intervention_id] = now
    for iid, ts in list(bucket.items()):
        if now - ts > _RESOLUTION_TTL_SECONDS:
            del bucket[iid]


def _resolved_ids(tenant_id: str | None) -> set[str]:
    """The set of still-fresh resolved intervention ids for a tenant."""
    now = time.time()
    bucket = _RESOLUTIONS.get(tenant_id, {})
    return {iid for iid, ts in bucket.items() if now - ts <= _RESOLUTION_TTL_SECONDS}


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def _sse(event: dict[str, Any]) -> str:
    """Format one event as an SSE ``data:`` frame."""
    return f"data: {json.dumps(event)}\n\n"


def _event_for_subscriber(
    event: dict[str, Any], tenant_id: str | None
) -> dict[str, Any] | None:
    """Scope a bus event to one subscriber's tenant.

    Producers tag tenant-owned events with an internal ``_tenant`` key. If that
    tag is set and does not match this subscriber's ``tenant_id``, the event is
    for another tenant and is dropped (returns ``None``). Otherwise a copy is
    returned WITHOUT the internal ``_tenant`` key so it never leaks to a client.
    An untagged event (``_tenant`` absent or ``None``) is broadcast to everyone.
    """
    event_tenant = event.get("_tenant")
    if event_tenant is not None and event_tenant != tenant_id:
        return None
    visible = dict(event)
    visible.pop("_tenant", None)
    return visible


async def _current_snapshot(gateway: Gateway, tenant_id: str | None) -> dict[str, Any]:
    """Read the roster (scoped to ``tenant_id``) and map it to a snapshot."""
    generated_at = _now_iso()
    if gateway.storage is None:
        return build_snapshot([], generated_at=generated_at)
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        rows = await workers_repository.read_roster(
            db,
            tenant_id=tenant_id,
            now=time.time(),
            ttl_seconds=DEFAULT_TTL_SECONDS,
        )
        # Recent sessions -> tasks; guardrail events -> interventions. Both are
        # tenant-scoped exactly like the roster above.
        sessions = await session_repository.list_sessions(
            db, tenant=tenant_id, limit=100
        )
        events = await guardrail_events_repository.list_events(
            db, tenant=tenant_id, limit=50
        )
    # Drop interventions the operator has already resolved so they do not
    # reappear on the next snapshot (their id is ``guardrail-{event.id}``).
    resolved = _resolved_ids(tenant_id)
    events = [e for e in events if f"guardrail-{e.id}" not in resolved]
    return build_snapshot(
        rows, sessions=sessions, interventions=events, generated_at=generated_at
    )


@router.get("/runtime-info")
async def runtime_info() -> dict[str, Any]:
    """Advertise the runtime and which OpenOrca capabilities it supports."""
    return {
        "runtime": "voicegateway",
        "language": "python",
        "supports": {"sse": True, "interventions": True, "snapshots": True},
    }


@router.get("/snapshot")
async def snapshot(
    request: Request,
    principal: Principal = Depends(require_principal),
) -> dict[str, Any]:
    """Return the current fleet snapshot, scoped to the caller's tenant."""
    gateway = get_gateway(request)
    tenant_id = resolve_read_tenant(principal, request.query_params.get("tenant"))
    return await _current_snapshot(gateway, tenant_id)


@router.post("/interventions/resolve")
async def resolve_intervention(
    request: Request,
    principal: Principal = Depends(require_principal),
) -> dict[str, str]:
    """Resolve an intervention: validate the request, record the resolution so
    the intervention drops out of later snapshots, and publish a TENANT-SCOPED
    ``intervention.resolved`` so only that tenant's dashboards update.

    Gated behind ``require_principal`` so a write requires the same auth as the
    reads. The tenant is taken from the principal (never the body), so a caller
    can only ever resolve their own tenant's interventions.
    """
    body: dict[str, Any] = await request.json()
    intervention_id = body.get("interventionId")
    action = body.get("action", "later")
    if not isinstance(intervention_id, str) or not intervention_id:
        raise HTTPException(status_code=400, detail="interventionId is required")
    if action not in _RESOLVE_ACTIONS:
        raise HTTPException(
            status_code=400, detail="action must be approve, deny, or later"
        )
    tenant_id = resolve_read_tenant(principal, request.query_params.get("tenant"))
    _mark_resolved(tenant_id, intervention_id)
    await bus.publish(
        {
            "type": "intervention.resolved",
            "interventionId": intervention_id,
            "resolution": action,
            "_tenant": tenant_id,
        }
    )
    return {"status": "resolved"}


@router.get("/events")
async def events(
    request: Request,
    principal: Principal = Depends(require_principal),
) -> StreamingResponse:
    """Stream fleet events to a dashboard client as Server-Sent Events."""
    gateway = get_gateway(request)
    tenant_id = resolve_read_tenant(principal, request.query_params.get("tenant"))

    async def _generate() -> Any:
        sub = bus.subscribe()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        sub.get(), timeout=_KEEPALIVE_SECONDS
                    )
                except TimeoutError:
                    # Idle keepalive: heartbeat + fold in a periodic fleet
                    # refresh so offline TTL transitions propagate. Both frames
                    # are built from _current_snapshot (already scoped to this
                    # subscriber's tenant), so they bypass the fan-out filter.
                    snap = await _current_snapshot(gateway, tenant_id)
                    yield _sse(
                        {"type": "fleet.updated", "fleetHealth": snap["fleetHealth"]}
                    )
                    yield _sse({"type": "runtime.status", "status": "connected"})
                    continue

                # Bus path: scope the event to this subscriber's tenant and
                # strip the internal tag before it reaches the client.
                visible = _event_for_subscriber(event, tenant_id)
                if visible is None:
                    continue
                if visible["type"] == "__resync__":
                    snap = await _current_snapshot(gateway, tenant_id)
                    visible = {"type": "snapshot.replace", "snapshot": snap}
                yield _sse(visible)
        finally:
            bus.unsubscribe(sub)
            await sub.aclose()

    return StreamingResponse(_generate(), media_type="text/event-stream")


__all__ = ["bus", "router"]
