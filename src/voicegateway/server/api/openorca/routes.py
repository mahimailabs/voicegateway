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

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from voicegateway.repository import workers_repository
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

_TTL_SECONDS = 45.0
_KEEPALIVE_SECONDS = 15.0


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def _sse(event: dict[str, Any]) -> str:
    """Format one event as an SSE ``data:`` frame."""
    return f"data: {json.dumps(event)}\n\n"


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
            ttl_seconds=_TTL_SECONDS,
        )
    return build_snapshot(rows, generated_at=generated_at)


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
async def resolve_intervention(request: Request) -> dict[str, str]:
    """Publish an intervention resolution to connected dashboards."""
    body: dict[str, Any] = await request.json()
    await bus.publish(
        {
            "type": "intervention.resolved",
            "interventionId": body.get("interventionId"),
            "resolution": body.get("action", "later"),
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
                    # refresh so offline TTL transitions propagate.
                    event = {"type": "runtime.status", "status": "connected"}
                    snap = await _current_snapshot(gateway, tenant_id)
                    yield _sse(
                        {"type": "fleet.updated", "fleetHealth": snap["fleetHealth"]}
                    )
                if event["type"] == "__resync__":
                    snap = await _current_snapshot(gateway, tenant_id)
                    event = {"type": "snapshot.replace", "snapshot": snap}
                yield _sse(event)
        finally:
            await sub.aclose()

    return StreamingResponse(_generate(), media_type="text/event-stream")


__all__ = ["bus", "router"]
