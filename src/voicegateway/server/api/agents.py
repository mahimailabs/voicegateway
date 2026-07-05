"""/v1/agents: fleet worker heartbeat + live roster.

Workers POST a presence payload to ``/heartbeat`` (write scope); the roster is
served from ``GET /agents`` (read scope). Both derive the tenant from the
authenticated api-key when one is present (``request.state.api_key_tenant_id``,
stamped by :func:`require_scope`), so a ``vk_`` key can only write and read its
own tenant. A no-credential operator (the self-hosted default) writes and reads
the unscoped roster.
"""

from __future__ import annotations

import time
from datetime import UTC
from typing import Any

from fastapi import APIRouter, Depends, Request

from voicegateway.repository import workers_repository
from voicegateway.server.api._deps import get_gateway, require_scope

router = APIRouter(prefix="/agents", tags=["agents"])

_TTL_SECONDS = 45.0


@router.post(
    "/heartbeat",
    status_code=202,
    dependencies=[Depends(require_scope("write"))],
)
async def heartbeat(request: Request) -> dict[str, str]:
    """Upsert the caller's worker presence row."""
    gateway = get_gateway(request)
    presence: dict[str, Any] = await request.json()

    # A vk_ key can only write its own tenant: prefer the authenticated tenant
    # over any tenant_id the caller put in the body.
    tenant_id = getattr(request.state, "api_key_tenant_id", None)
    if tenant_id is not None:
        presence = {**presence, "tenant_id": tenant_id}

    if gateway.storage is not None:
        await gateway.storage._ensure_initialized()
        async with gateway.storage._conn.session() as db:
            await workers_repository.upsert_heartbeat(db, presence)
        await _publish_fleet_update(gateway, presence)

    return {"status": "accepted"}


async def _publish_fleet_update(gateway: Any, presence: dict[str, Any]) -> None:
    """Push agent/fleet updates to connected OpenOrca dashboards.

    Imported lazily to avoid an import cycle (the openorca routes module
    imports shared api dependencies that would otherwise pull this module in
    at import time). A failure here must never break the heartbeat write.
    """
    from datetime import datetime

    from voicegateway.server.api.openorca.mapper import build_snapshot
    from voicegateway.server.api.openorca.routes import bus

    tenant_id = presence.get("tenant_id")
    async with gateway.storage._conn.session() as db:
        rows = await workers_repository.read_roster(
            db,
            tenant_id=tenant_id,
            now=time.time(),
            ttl_seconds=_TTL_SECONDS,
        )
    snap = build_snapshot(rows, generated_at=datetime.now(UTC).isoformat())
    agent = next(
        (a for a in snap["agents"] if a["id"] == presence.get("agent_name")),
        None,
    )
    if agent is not None:
        await bus.publish({"type": "agent.updated", "agent": agent})
    await bus.publish({"type": "fleet.updated", "fleetHealth": snap["fleetHealth"]})


@router.get("", dependencies=[Depends(require_scope("read"))])
async def list_agents(request: Request) -> dict[str, list[dict[str, Any]]]:
    """Return the live worker roster, scoped to the caller's tenant if any."""
    gateway = get_gateway(request)
    tenant_id = getattr(request.state, "api_key_tenant_id", None)

    workers: list[dict[str, Any]] = []
    if gateway.storage is not None:
        await gateway.storage._ensure_initialized()
        async with gateway.storage._conn.session() as db:
            roster = await workers_repository.read_roster(
                db,
                tenant_id=tenant_id,
                now=time.time(),
                ttl_seconds=_TTL_SECONDS,
            )
        workers = [
            {
                "agent_id": row.agent_id,
                "agent_name": row.agent_name,
                "project": row.project,
                "region": row.region,
                "version": row.version,
                "host": row.host,
                "active_sessions": row.active_sessions,
                "status": row.status,
                "started_at": row.started_at,
                "last_seen": row.last_seen,
            }
            for row in roster
        ]

    return {"workers": workers}


__all__ = ["router"]
