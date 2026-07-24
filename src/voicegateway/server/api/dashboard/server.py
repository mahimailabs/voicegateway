"""Dashboard endpoint: GET /api/server/overview.

A read-only, non-billing snapshot of the LiveKit deployment the metered agents
run on, annotated with VoiceGateway's own cost / latency. LiveKit's own console
shows the topology; it does not know cost, so this page joins the two.

Sections are shaped ``{ok, error, ...}`` independently so one failing read never
blanks the page. Everything here is a cheap control-plane list or a local DB
read: NO synthetic probes and NO billed calls (those stay on Diagnostics).

Gated behind ``require_scope(ADMIN_SCOPE)`` like Diagnostics: it reveals the
LiveKit URL and the deployment's control-plane topology. That gate is a no-op
when no API keys are configured (the local single-operator default), and
enforces the admin scope once auth is enabled.

Honesty: this endpoint never fabricates SFU node health or load. The only SFU
number VG can produce is a timestamped, billed probe, which lives on Diagnostics.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends

from voicegateway.core.auth import ADMIN_SCOPE
from voicegateway.livekit_diag.config import CredsError, LiveKitCreds, resolve_creds
from voicegateway.repository import workers_repository
from voicegateway.repository.workers_repository import DEFAULT_TTL_SECONDS
from voicegateway.server.api._deps import get_gateway, require_scope
from voicegateway.utils.percentiles import compute_percentiles

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway
    from voicegateway.repository.workers_repository import RosterRow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/server", tags=["dashboard"])

# Cost / latency annotation window for a live room (last 24h).
_ROOM_WINDOW_SECONDS = 86_400.0


# Seams for tests to override (monkeypatch), mirror diagnostics._resolve_creds.
def _resolve_creds() -> LiveKitCreds:
    return resolve_creds(None, None, None)


def _make_admin(creds: LiveKitCreds) -> Any | None:
    """Construct a LiveKitAdmin, or None when the ``[livekit]`` extra is absent.

    The livekit server SDK ships with the ``[livekit]`` extra, so import it lazily
    here rather than at module top: a missing dependency must degrade the rooms
    section to an install hint, never crash the dashboard router import.
    """
    try:
        from voicegateway.livekit_diag.admin import LiveKitAdmin
    except ImportError:
        return None
    return LiveKitAdmin(creds)


def _memory_pct(rss: int | None, total: int | None) -> float | None:
    """RSS as a percentage of the memory ceiling (None when unavailable)."""
    if not rss or not total:
        return None
    return round(rss / total * 100, 1)


async def _room_cost(gateway: Gateway, room: str, now: float) -> dict[str, Any]:
    """Aggregate VG's metered cost / requests / p95 latency for a room (24h).

    Best-effort enrichment: any read failure yields zeros rather than failing the
    whole rooms section. This is the wedge LiveKit's own console cannot show.
    """
    empty: dict[str, Any] = {"cost_usd": 0.0, "request_count": 0, "p95_latency_ms": None}
    storage = gateway.storage
    if storage is None:
        return empty
    try:
        # Bound the scan in SQL (timestamp >= cutoff) so a long-lived room's
        # roll-up stays cheap as the requests table grows.
        rows = await storage.get_requests_for_room(room, since=now - _ROOM_WINDOW_SECONDS)
    except Exception:  # noqa: BLE001 - cost annotation must never blank the page
        return empty
    cost = sum((r.get("cost_usd") or 0.0) for r in rows)
    latencies = [
        float(r["total_latency_ms"])
        for r in rows
        if r.get("total_latency_ms") is not None
    ]
    # Use the shared percentile helper so a room's p95 matches the p95 the
    # Latency / Agents pages compute for the same underlying rows.
    p95 = compute_percentiles(latencies, [95.0])["p95"] if latencies else None
    return {
        "cost_usd": round(cost, 6),
        "request_count": len(rows),
        "p95_latency_ms": p95,
    }


async def _rooms_section(admin: Any, gateway: Gateway, now: float) -> dict[str, Any]:
    """Live rooms + in-room agents (LiveKit Server API), annotated with VG cost."""
    try:
        agent_rows = await admin.list_agents()
    except Exception as exc:  # noqa: BLE001 - a control-plane read degrades, never 500s
        return {"ok": False, "error": str(exc), "rooms": []}
    grouped: dict[str, dict[str, Any]] = {}
    for r in agent_rows:
        room = grouped.setdefault(
            r.room, {"name": r.room, "humans": r.humans, "agents": []}
        )
        room["agents"].append(
            {
                "agent_name": r.agent_name,
                "identity": r.identity,
                "state": r.state,
                "age_s": r.age_s,
            }
        )
    rooms = sorted(grouped.values(), key=lambda x: x["name"])
    # Enrich concurrently: each _room_cost is an independent windowed read.
    costs = await asyncio.gather(*(_room_cost(gateway, r["name"], now) for r in rooms))
    for room, cost in zip(rooms, costs, strict=True):
        room.update(cost)
    return {"ok": True, "error": None, "rooms": rooms}


async def _egress_section(admin: Any) -> dict[str, Any]:
    """Active/recent egress jobs (recording/streaming)."""
    try:
        rows = await admin.list_egress()
    except Exception as exc:  # noqa: BLE001 - a control-plane read degrades, never 500s
        return {"ok": False, "error": str(exc), "items": []}
    return {"ok": True, "error": None, "items": [dataclasses.asdict(r) for r in rows]}


async def _ingress_section(admin: Any) -> dict[str, Any]:
    """Configured ingress endpoints (WHIP / RTMP / URL)."""
    try:
        rows = await admin.list_ingress()
    except Exception as exc:  # noqa: BLE001 - a control-plane read degrades, never 500s
        return {"ok": False, "error": str(exc), "items": []}
    return {"ok": True, "error": None, "items": [dataclasses.asdict(r) for r in rows]}


async def _sip_section(admin: Any) -> dict[str, Any]:
    """SIP inbound/outbound trunks and dispatch rules (telephony wiring)."""
    try:
        inbound, outbound, rules = await asyncio.gather(
            admin.list_sip_inbound_trunks(),
            admin.list_sip_outbound_trunks(),
            admin.list_sip_dispatch_rules(),
        )
    except Exception as exc:  # noqa: BLE001 - a control-plane read degrades, never 500s
        return {
            "ok": False,
            "error": str(exc),
            "inbound": [],
            "outbound": [],
            "dispatch_rules": [],
        }
    return {
        "ok": True,
        "error": None,
        "inbound": [dataclasses.asdict(r) for r in inbound],
        "outbound": [dataclasses.asdict(r) for r in outbound],
        "dispatch_rules": [dataclasses.asdict(r) for r in rules],
    }


def _absent_sections(error: str) -> dict[str, dict[str, Any]]:
    """The LiveKit section shapes when nothing could be read (creds/SDK absent)."""
    return {
        "rooms": {"ok": False, "error": error, "rooms": []},
        "egress": {"ok": False, "error": error, "items": []},
        "ingress": {"ok": False, "error": error, "items": []},
        "sip": {
            "ok": False,
            "error": error,
            "inbound": [],
            "outbound": [],
            "dispatch_rules": [],
        },
    }


async def _livekit_snapshot(
    gateway: Gateway, creds: LiveKitCreds | None, now: float
) -> tuple[dict[str, dict[str, Any]], bool | None]:
    """Every LiveKit control-plane read through ONE admin client.

    Returns ``(sections, reachable)`` where sections = {rooms, egress, ingress,
    sip}. ``reachable`` is a concrete True/False only when reads were attempted
    (True if any section answered), and stays ``None`` when nothing probed the
    deployment (creds absent, or the ``[livekit]`` extra missing), so the UI
    never reports an "unreachable" state VG never measured.
    """
    if creds is None:
        return _absent_sections("LiveKit not configured"), None
    try:
        admin = _make_admin(creds)
    except Exception as exc:  # noqa: BLE001 - client construction must not 500
        return _absent_sections(str(exc)), False
    if admin is None:
        return (
            _absent_sections("LiveKit SDK not installed. Install voicegateway[livekit]."),
            None,
        )
    try:
        rooms, egress, ingress, sip = await asyncio.gather(
            _rooms_section(admin, gateway, now),
            _egress_section(admin),
            _ingress_section(admin),
            _sip_section(admin),
        )
    finally:
        # Best-effort teardown: a transport-close error must neither discard the
        # sections above nor 500 the endpoint.
        try:
            await admin.aclose()
        except Exception:  # noqa: BLE001
            logger.debug("LiveKitAdmin.aclose() failed", exc_info=True)
    sections = {"rooms": rooms, "egress": egress, "ingress": ingress, "sip": sip}
    reachable = any(s["ok"] for s in sections.values())
    return sections, reachable


def _worker_entry(w: RosterRow) -> dict[str, Any]:
    return {
        "agent_id": w.agent_id,
        "agent_name": w.agent_name,
        "region": w.region,
        "host": w.host,
        "version": w.version,
        "status": w.status,
        "active_sessions": w.active_sessions,
        "last_seen": w.last_seen,
        "memory_pct": _memory_pct(w.memory_rss_bytes, w.memory_total_bytes),
    }


def _fleet_counts(roster: list[RosterRow]) -> dict[str, int]:
    counts = {"total": len(roster), "idle": 0, "busy": 0, "offline": 0}
    for w in roster:
        if w.status in counts:
            counts[w.status] += 1
    return counts


async def _fleet_section(gateway: Gateway, now: float) -> dict[str, Any]:
    """Registered worker roster from the local DB heartbeats (idle/busy/offline).

    This is VG-native, not a LiveKit call: the LiveKit Server API does not report
    idle/registered workers. Empty when no agents heartbeat to this collector.
    """
    storage = gateway.storage
    if storage is None:
        return {"ok": True, "error": None, "workers": [], "counts": _fleet_counts([])}
    try:
        await storage._ensure_initialized()
        async with storage._conn.session() as db:
            roster = await workers_repository.read_roster(
                db, tenant_id=None, now=now, ttl_seconds=DEFAULT_TTL_SECONDS
            )
    except Exception as exc:  # noqa: BLE001 - a section read degrades, never 500s
        return {
            "ok": False,
            "error": str(exc),
            "workers": [],
            "counts": _fleet_counts([]),
        }
    return {
        "ok": True,
        "error": None,
        "workers": [_worker_entry(w) for w in roster],
        "counts": _fleet_counts(roster),
    }


@router.get("/overview")
async def get_server_overview(
    gateway: Gateway = Depends(get_gateway),
    _auth: None = Depends(require_scope(ADMIN_SCOPE)),
) -> dict[str, Any]:
    """Read-only snapshot of the LiveKit deployment + VG-metered cost, per section."""
    now = time.time()
    creds: LiveKitCreds | None
    try:
        creds = _resolve_creds()
    except CredsError:
        creds = None

    connection: dict[str, Any] = (
        {"configured": True, "url": creds.url}
        if creds is not None
        else {"configured": False, "url": None}
    )

    sections, reachable = await _livekit_snapshot(gateway, creds, now)
    fleet = await _fleet_section(gateway, now)
    # reachable is None unless a control-plane read was actually attempted, so
    # the UI never labels an unmeasured deployment "unreachable".
    connection["reachable"] = reachable
    return {
        "generated_at": now,
        "connection": connection,
        "rooms": sections["rooms"],
        "egress": sections["egress"],
        "ingress": sections["ingress"],
        "sip": sections["sip"],
        "fleet": fleet,
    }
