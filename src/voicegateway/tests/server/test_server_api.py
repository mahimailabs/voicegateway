"""Tests for the dashboard ``GET /api/server/overview`` endpoint.

The Server page is a read-only, non-billing snapshot: live LiveKit rooms/agents
(control-plane reads) + the local worker roster, each annotated with VG cost.
Tests monkeypatch the two seams (``_resolve_creds`` and ``_make_admin``) so no
real LiveKit server or the optional ``[livekit]`` extra is required, and seed the
worker roster / room cost through the gateway's own storage.
"""

from __future__ import annotations

import time
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.livekit_diag.admin import (
    EgressRow,
    IngressRow,
    SipDispatchRuleRow,
    SipInboundTrunkRow,
    SipOutboundTrunkRow,
)
from voicegateway.livekit_diag.config import CredsError, LiveKitCreds
from voicegateway.models.request_model import RequestRecord
from voicegateway.repository import workers_repository
from voicegateway.server.api.dashboard import server as server_api
from voicegateway.server.main import build_app

_FAKE_CREDS = LiveKitCreds("wss://lk.example", "k", "s")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "server-test.db"))
    return Gateway(config_path=temp_config)


@pytest.fixture
async def client(gateway):
    app = build_app(gateway, enable_mcp_sse=False, enable_dashboard=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _configured(monkeypatch) -> None:
    monkeypatch.setattr(server_api, "_resolve_creds", lambda: _FAKE_CREDS)


def _not_configured(monkeypatch) -> None:
    def _raise() -> LiveKitCreds:
        raise CredsError("no creds")

    monkeypatch.setattr(server_api, "_resolve_creds", _raise)


class _FakeAdmin:
    """A LiveKitAdmin stand-in that returns canned rows without a real server.

    ``raises`` fails EVERY control-plane read (a down/unreachable server);
    ``agents_raises`` fails only list_agents (one section down, others fine).
    """

    def __init__(
        self,
        rows: list[Any] | None = None,
        raises: Exception | None = None,
        agents_raises: Exception | None = None,
        sip_raises: Exception | None = None,
        aclose_raises: Exception | None = None,
        egress: list[Any] | None = None,
        ingress: list[Any] | None = None,
        sip: dict[str, list[Any]] | None = None,
    ):
        self._rows = rows or []
        self._raises = raises
        self._agents_raises = agents_raises
        self._sip_raises = sip_raises
        self._aclose_raises = aclose_raises
        self._egress = egress or []
        self._ingress = ingress or []
        self._sip = sip or {"inbound": [], "outbound": [], "dispatch_rules": []}
        self.closed = False

    def _guard(self) -> None:
        if self._raises is not None:
            raise self._raises

    def _sip_guard(self) -> None:
        self._guard()
        if self._sip_raises is not None:
            raise self._sip_raises

    async def list_agents(self) -> list[Any]:
        self._guard()
        if self._agents_raises is not None:
            raise self._agents_raises
        return self._rows

    async def list_egress(self) -> list[Any]:
        self._guard()
        return self._egress

    async def list_ingress(self) -> list[Any]:
        self._guard()
        return self._ingress

    async def list_sip_inbound_trunks(self) -> list[Any]:
        self._sip_guard()
        return self._sip["inbound"]

    async def list_sip_outbound_trunks(self) -> list[Any]:
        self._sip_guard()
        return self._sip["outbound"]

    async def list_sip_dispatch_rules(self) -> list[Any]:
        self._sip_guard()
        return self._sip["dispatch_rules"]

    async def aclose(self) -> None:
        self.closed = True
        if self._aclose_raises is not None:
            raise self._aclose_raises


def _capture_admin(monkeypatch, admin: _FakeAdmin) -> _FakeAdmin:
    """Install a _make_admin seam returning ``admin`` and hand it back for asserts."""
    monkeypatch.setattr(server_api, "_make_admin", lambda creds: admin)
    return admin


def _agent_row(agent_name: str, room: str, state: str, humans: int) -> SimpleNamespace:
    return SimpleNamespace(
        agent_name=agent_name,
        room=room,
        identity=f"{agent_name}-id",
        state=state,
        humans=humans,
        age_s=12.0,
    )


async def _seed_worker(
    gateway: Gateway,
    agent_id: str,
    status: str,
    region: str,
    *,
    stale: bool = False,
) -> None:
    # A stale heartbeat (aged past the TTL) must be served "offline" by
    # read_roster regardless of its seeded status.
    ts = time.time() - 3600 if stale else time.time()
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        await workers_repository.upsert_heartbeat(
            db,
            {
                "agent_id": agent_id,
                "agent_name": agent_id,
                "region": region,
                "version": "0.4.0",
                "host": "host-1",
                "active_sessions": 1 if status == "busy" else 0,
                "status": status,
                "memory_rss_bytes": 100,
                "memory_total_bytes": 1000,
                "ts": ts,
            },
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_overview_not_configured(client, monkeypatch):
    _not_configured(monkeypatch)
    resp = await client.get("/api/server/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["connection"] == {"configured": False, "url": None, "reachable": None}
    assert data["rooms"]["ok"] is False
    assert "not configured" in data["rooms"]["error"].lower()
    assert data["rooms"]["rooms"] == []
    # Every LiveKit section degrades together when creds are absent.
    for sec in ("egress", "ingress", "sip"):
        assert data[sec]["ok"] is False
    assert data["egress"]["items"] == []
    assert data["sip"]["inbound"] == []
    # Fleet is a local DB read, so it is fine even when LiveKit is not configured.
    assert data["fleet"]["ok"] is True
    assert data["fleet"]["workers"] == []


async def test_overview_rooms_grouped_and_cost_annotated(client, gateway, monkeypatch):
    _configured(monkeypatch)
    rows = [
        _agent_row("receptionist", "room-a", "active", humans=2),
        _agent_row("scheduler", "room-a", "dispatched", humans=2),
        _agent_row("receptionist", "room-b", "active", humans=1),
    ]
    admin = _capture_admin(monkeypatch, _FakeAdmin(rows))

    now = time.time()

    async def _fake_room_requests(room: str, since: float | None = None):
        canned = {
            "room-a": [
                {"timestamp": now, "cost_usd": 0.01, "total_latency_ms": 100.0},
                {"timestamp": now, "cost_usd": 0.02, "total_latency_ms": 300.0},
                # A stale row outside the 24h window: the SQL `since` bound drops it.
                {"timestamp": now - 200_000, "cost_usd": 5.0, "total_latency_ms": 900.0},
            ]
        }.get(room, [])
        # Mirror the repository's `since` semantics so this test exercises that
        # _room_cost passes the window cutoff, not just that it sums rows.
        return [r for r in canned if since is None or r["timestamp"] >= since]

    monkeypatch.setattr(gateway.storage, "get_requests_for_room", _fake_room_requests)

    resp = await client.get("/api/server/overview")
    assert resp.status_code == 200
    data = resp.json()

    assert data["connection"]["configured"] is True
    assert data["connection"]["url"] == "wss://lk.example"
    assert data["connection"]["reachable"] is True
    assert data["rooms"]["ok"] is True

    rooms = {r["name"]: r for r in data["rooms"]["rooms"]}
    assert set(rooms) == {"room-a", "room-b"}
    room_a = rooms["room-a"]
    assert room_a["humans"] == 2
    assert {a["agent_name"] for a in room_a["agents"]} == {"receptionist", "scheduler"}
    # Cost sums only the two in-window rows (the 200k-second-old row is dropped).
    assert room_a["cost_usd"] == pytest.approx(0.03)
    assert room_a["request_count"] == 2
    # p95 via the shared linear-interpolation helper: p95 of [100, 300] = 290.0
    # (must match the Latency / Agents pages, not a bespoke nearest-rank).
    assert room_a["p95_latency_ms"] == pytest.approx(290.0)
    # room-b has no metered requests: zero cost, not a crash.
    assert rooms["room-b"]["cost_usd"] == 0.0
    assert rooms["room-b"]["request_count"] == 0
    # The LiveKit client is closed after a successful read.
    assert admin.closed is True


async def test_overview_room_cost_windowed_real_db(client, gateway, monkeypatch):
    """End-to-end: per-room cost is bounded by the 24h SQL window (real DB read).

    Unlike the grouped test (which stubs get_requests_for_room), this seeds real
    request rows and lets _room_cost run the actual `since`-bounded query, proving
    the stale row is dropped in SQL, not just in Python.
    """
    _configured(monkeypatch)
    _capture_admin(
        monkeypatch, _FakeAdmin([_agent_row("bot", "room-real", "active", humans=1)])
    )
    now = time.time()

    async def _log(ts: float, cost: float, latency: float) -> None:
        await gateway.storage.log_request(
            RequestRecord(
                id=str(uuid.uuid4()),
                timestamp=ts,
                modality="llm",
                model_id="openai/gpt-4o-mini",
                provider="openai",
                project="default",
                cost_usd=cost,
                total_latency_ms=latency,
                metadata={"room": "room-real"},
                session_id=f"vg-{uuid.uuid4()}",
            )
        )

    await _log(now, 0.01, 100.0)  # in window
    await _log(now - 1_000, 0.02, 300.0)  # in window (older, still < 24h)
    await _log(now - 200_000, 5.0, 900.0)  # > 24h old: dropped by the SQL `since`

    resp = await client.get("/api/server/overview")
    assert resp.status_code == 200
    rooms = {r["name"]: r for r in resp.json()["rooms"]["rooms"]}
    room = rooms["room-real"]
    assert room["request_count"] == 2
    assert room["cost_usd"] == pytest.approx(0.03)
    assert room["p95_latency_ms"] == pytest.approx(290.0)


async def test_overview_rooms_degrade_but_others_ok(client, monkeypatch):
    """One failing LiveKit read is a section error; the others still answer."""
    _configured(monkeypatch)
    admin = _capture_admin(
        monkeypatch, _FakeAdmin(agents_raises=RuntimeError("agents read boom"))
    )
    resp = await client.get("/api/server/overview")
    # The endpoint must never 500: a failed control-plane read is a section error.
    assert resp.status_code == 200
    data = resp.json()
    assert data["rooms"]["ok"] is False
    assert "boom" in data["rooms"]["error"]
    # egress/ingress/sip answered, so the deployment is reachable.
    assert data["egress"]["ok"] is True
    assert data["ingress"]["ok"] is True
    assert data["sip"]["ok"] is True
    assert data["connection"]["reachable"] is True
    # Fleet still succeeds independently.
    assert data["fleet"]["ok"] is True
    # The client is closed even on the error path (finally guard).
    assert admin.closed is True


async def test_overview_unreachable_when_all_reads_fail(client, monkeypatch):
    """Every control-plane read failing means the server is unreachable."""
    _configured(monkeypatch)
    admin = _capture_admin(
        monkeypatch, _FakeAdmin(raises=RuntimeError("connection refused"))
    )
    resp = await client.get("/api/server/overview")
    assert resp.status_code == 200
    data = resp.json()
    for sec in ("rooms", "egress", "ingress", "sip"):
        assert data[sec]["ok"] is False
    assert data["connection"]["reachable"] is False
    # Fleet is a local read, unaffected by LiveKit being down.
    assert data["fleet"]["ok"] is True
    assert admin.closed is True


async def test_overview_survives_aclose_error(client, monkeypatch):
    """A transport-close error during teardown must not 500 the endpoint."""
    _configured(monkeypatch)
    admin = _capture_admin(
        monkeypatch,
        _FakeAdmin(
            rows=[_agent_row("a", "room-a", "active", humans=1)],
            aclose_raises=RuntimeError("teardown boom"),
        ),
    )
    resp = await client.get("/api/server/overview")
    assert resp.status_code == 200
    data = resp.json()
    # list_agents succeeded, so the section is ok despite the teardown error.
    assert data["rooms"]["ok"] is True
    assert {r["name"] for r in data["rooms"]["rooms"]} == {"room-a"}
    assert admin.closed is True


async def test_overview_sdk_absent(client, monkeypatch):
    _configured(monkeypatch)
    monkeypatch.setattr(server_api, "_make_admin", lambda creds: None)
    resp = await client.get("/api/server/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["rooms"]["ok"] is False
    assert "not installed" in data["rooms"]["error"].lower()
    # Every LiveKit section carries the same install hint.
    for sec in ("egress", "ingress", "sip"):
        assert data[sec]["ok"] is False
        assert "not installed" in data[sec]["error"].lower()
    # The SDK was never even loaded, so nothing probed the control plane:
    # reachable stays null (the UI shows "Configured", not "Unreachable").
    assert data["connection"]["reachable"] is None


async def test_overview_fleet_roster(client, gateway, monkeypatch):
    _not_configured(monkeypatch)  # focus on fleet; rooms section is empty
    await _seed_worker(gateway, "agent-idle", "idle", "us-east")
    await _seed_worker(gateway, "agent-busy", "busy", "eu-west")
    # A stale heartbeat is derived offline at read time, regardless of status.
    await _seed_worker(gateway, "agent-gone", "idle", "ap-south", stale=True)

    resp = await client.get("/api/server/overview")
    assert resp.status_code == 200
    fleet = resp.json()["fleet"]
    assert fleet["ok"] is True
    assert fleet["counts"] == {"total": 3, "idle": 1, "busy": 1, "offline": 1}
    by_id = {w["agent_id"]: w for w in fleet["workers"]}
    assert by_id["agent-busy"]["active_sessions"] == 1
    assert by_id["agent-idle"]["region"] == "us-east"
    assert by_id["agent-idle"]["memory_pct"] == 10.0
    assert by_id["agent-gone"]["status"] == "offline"


async def test_overview_livekit_sections_populated(client, monkeypatch):
    """Egress / Ingress / SIP serialize their whitelisted rows; no secrets leak."""
    _configured(monkeypatch)
    _capture_admin(
        monkeypatch,
        _FakeAdmin(
            rows=[],
            egress=[
                EgressRow(
                    egress_id="eg1",
                    status="EGRESS_ACTIVE",
                    source_type="EGRESS_SOURCE_TYPE_WEB",
                    room_name="r1",
                    started_at=123,
                )
            ],
            ingress=[
                IngressRow(
                    ingress_id="in1",
                    name="stream",
                    input_type="RTMP_INPUT",
                    room_name="r2",
                    status="ENDPOINT_PUBLISHING",
                )
            ],
            sip={
                "inbound": [
                    SipInboundTrunkRow(
                        trunk_id="tin1", name="main-in", numbers=["+15551234567"]
                    )
                ],
                "outbound": [
                    SipOutboundTrunkRow(
                        trunk_id="tout1",
                        name="main-out",
                        address="sip.example.com",
                        transport="SIP_TRANSPORT_TCP",
                        numbers=["+15559876543"],
                    )
                ],
                "dispatch_rules": [
                    SipDispatchRuleRow(rule_id="dr1", name="rule", trunk_ids=["tin1"])
                ],
            },
        ),
    )
    resp = await client.get("/api/server/overview")
    assert resp.status_code == 200
    data = resp.json()

    # Exact-dict equality doubles as a secret tripwire: any extra serialized
    # field (a leaked secret, a schema drift) fails the test.
    assert data["egress"]["ok"] is True
    assert data["egress"]["items"][0] == {
        "egress_id": "eg1",
        "status": "EGRESS_ACTIVE",
        "source_type": "EGRESS_SOURCE_TYPE_WEB",
        "room_name": "r1",
        "started_at": 123,
    }
    assert data["ingress"]["items"][0] == {
        "ingress_id": "in1",
        "name": "stream",
        "input_type": "RTMP_INPUT",
        "room_name": "r2",
        "status": "ENDPOINT_PUBLISHING",
    }
    sip = data["sip"]
    assert sip["inbound"][0] == {
        "trunk_id": "tin1",
        "name": "main-in",
        "numbers": ["+15551234567"],
    }
    assert sip["outbound"][0] == {
        "trunk_id": "tout1",
        "name": "main-out",
        "address": "sip.example.com",
        "transport": "SIP_TRANSPORT_TCP",
        "numbers": ["+15559876543"],
    }
    assert sip["dispatch_rules"][0] == {
        "rule_id": "dr1",
        "name": "rule",
        "trunk_ids": ["tin1"],
    }


async def test_overview_sip_degrades_but_others_ok(client, monkeypatch):
    """A SIP sub-read failure degrades only SIP; rooms/egress/ingress still answer."""
    _configured(monkeypatch)
    admin = _capture_admin(
        monkeypatch, _FakeAdmin(sip_raises=RuntimeError("sip service disabled"))
    )
    resp = await client.get("/api/server/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sip"]["ok"] is False
    assert "sip service disabled" in data["sip"]["error"]
    assert data["sip"]["inbound"] == []
    # The other LiveKit reads still succeeded, so the deployment is reachable.
    assert data["rooms"]["ok"] is True
    assert data["egress"]["ok"] is True
    assert data["ingress"]["ok"] is True
    assert data["connection"]["reachable"] is True
    assert admin.closed is True


async def test_overview_fleet_degrades_on_read_error(client, monkeypatch):
    """A failing fleet read is a section error, not a 500 (symmetric with rooms)."""
    _not_configured(monkeypatch)

    async def _boom(*args, **kwargs):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(workers_repository, "read_roster", _boom)
    resp = await client.get("/api/server/overview")
    assert resp.status_code == 200
    fleet = resp.json()["fleet"]
    assert fleet["ok"] is False
    assert "exploded" in fleet["error"]
    assert fleet["workers"] == []
    assert fleet["counts"] == {"total": 0, "idle": 0, "busy": 0, "offline": 0}


async def test_overview_requires_admin_when_auth_enabled(gateway, monkeypatch):
    """With static keys configured, the endpoint rejects unauthenticated callers.

    require_scope(ADMIN_SCOPE) is a no-op when no keys are configured (the local
    OSS default). Once keys exist it enforces admin, so an unauthenticated caller
    cannot read the configured LiveKit URL or deployment topology.
    """
    from voicegateway.core.auth import ADMIN_SCOPE, ApiKey

    _not_configured(monkeypatch)
    app = build_app(gateway, enable_mcp_sse=False, enable_dashboard=True)
    app.state.api_keys = [
        ApiKey(token="admin-secret-token", name="ops", scopes=(ADMIN_SCOPE,))
    ]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/api/server/overview")).status_code == 401
        ok = await c.get(
            "/api/server/overview",
            headers={"Authorization": "Bearer admin-secret-token"},
        )
        assert ok.status_code == 200
        assert ok.json()["connection"]["configured"] is False


async def test_overview_forbids_non_admin_scope_when_auth_enabled(gateway, monkeypatch):
    """A valid key lacking ADMIN_SCOPE is authenticated but forbidden (403).

    Guards the endpoint's deliberate ADMIN_SCOPE choice (it exposes the LiveKit
    URL + topology): a regression that downgraded it to a read scope would let a
    read-only key through, and this is the only test that would catch it.
    """
    from voicegateway.core.auth import ADMIN_SCOPE, ApiKey

    _not_configured(monkeypatch)
    app = build_app(gateway, enable_mcp_sse=False, enable_dashboard=True)
    app.state.api_keys = [
        ApiKey(token="admin-token", name="ops", scopes=(ADMIN_SCOPE,)),
        ApiKey(token="read-token", name="viewer", scopes=("read",)),
    ]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        forbidden = await c.get(
            "/api/server/overview",
            headers={"Authorization": "Bearer read-token"},
        )
        assert forbidden.status_code == 403
