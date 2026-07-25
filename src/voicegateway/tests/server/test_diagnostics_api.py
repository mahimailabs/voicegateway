"""Tests for the dashboard ``/api/diagnostics/*`` endpoints.

The diagnostics router runs LiveKit probes as background asyncio tasks against
a module-level in-memory registry. Tests monkeypatch the two seams
(_resolve_creds and _make_probes) so no real LiveKit server is needed.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.livekit_diag.config import CredsError, LiveKitCreds
from voicegateway.server.main import build_app

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "diag-test.db"))
    return Gateway(config_path=temp_config)


@pytest.fixture
async def client(gateway):
    app = build_app(gateway, enable_mcp_sse=False, enable_dashboard=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_registry():
    from voicegateway.server.api.dashboard import diagnostics as d

    d._RUNS.clear()
    d._ORDER.clear()
    d._TASKS.clear()
    yield
    d._RUNS.clear()
    d._ORDER.clear()
    d._TASKS.clear()


# ---------------------------------------------------------------------------
# Fake probes
# ---------------------------------------------------------------------------

_FAKE_CREDS = LiveKitCreds("wss://x", "k", "s")


class _FakeProbes:
    """Fast probes that return valid shapes without touching a real server."""

    async def agents(self, creds: Any) -> dict[str, Any]:
        return {"agents": []}

    async def sfu(
        self, creds: Any, load: bool, config: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "baseline": {"rtt_ms": 5.0, "loss_pct": 0.0, "quality": "Excellent"},
            "ramp": [],
            "knee": None,
        }

    async def latency(self, creds: Any, config: dict[str, Any]) -> dict[str, Any]:
        return {"agents": []}


class _SlowProbes(_FakeProbes):
    """Probes whose agents check blocks long enough to test 409 detection."""

    async def agents(self, creds: Any) -> dict[str, Any]:
        await asyncio.sleep(0.3)
        return {"agents": []}


class _FailLatencyProbes(_FakeProbes):
    """Probes where latency raises so that check fails while agents passes."""

    async def latency(self, creds: Any, config: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("latency probe failed")


# ---------------------------------------------------------------------------
# Helper: poll until terminal
# ---------------------------------------------------------------------------


async def _poll_until_done(client: AsyncClient, run_id: str) -> dict[str, Any]:
    """Poll GET /api/diagnostics/runs/{run_id} until status is terminal."""
    for _ in range(200):
        resp = await client.get(f"/api/diagnostics/runs/{run_id}")
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] in ("done", "failed"):
            return data
        await asyncio.sleep(0.01)
    raise AssertionError(f"run {run_id} did not reach terminal state in time")


# ---------------------------------------------------------------------------
# Tests: GET /api/diagnostics/creds
# ---------------------------------------------------------------------------


async def test_creds_reports_not_configured(client, monkeypatch):
    from voicegateway.server.api.dashboard import diagnostics

    monkeypatch.setattr(
        diagnostics,
        "_resolve_creds",
        lambda: (_ for _ in ()).throw(CredsError("no creds")),
    )
    resp = await client.get("/api/diagnostics/creds")
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is False
    assert data["url"] is None


async def test_creds_reports_configured(client, monkeypatch):
    from voicegateway.server.api.dashboard import diagnostics

    monkeypatch.setattr(diagnostics, "_resolve_creds", lambda: _FAKE_CREDS)
    resp = await client.get("/api/diagnostics/creds")
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is True
    assert data["url"] == "wss://x"


# ---------------------------------------------------------------------------
# Tests: POST /api/diagnostics/runs
# ---------------------------------------------------------------------------


async def test_run_rejects_when_not_configured(client, monkeypatch):
    from voicegateway.server.api.dashboard import diagnostics

    monkeypatch.setattr(
        diagnostics,
        "_resolve_creds",
        lambda: (_ for _ in ()).throw(CredsError("no creds")),
    )
    resp = await client.post(
        "/api/diagnostics/runs", json={"checks": ["agents"], "config": {}}
    )
    assert resp.status_code == 400
    assert "LiveKit not configured" in resp.json()["detail"]


async def test_run_rejects_empty_checks(client, monkeypatch):
    from voicegateway.server.api.dashboard import diagnostics

    monkeypatch.setattr(diagnostics, "_resolve_creds", lambda: _FAKE_CREDS)
    monkeypatch.setattr(diagnostics, "_make_probes", lambda _store: _FakeProbes())
    resp = await client.post("/api/diagnostics/runs", json={"checks": [], "config": {}})
    assert resp.status_code == 400


async def test_run_rejects_bad_checks(client, monkeypatch):
    from voicegateway.server.api.dashboard import diagnostics

    monkeypatch.setattr(diagnostics, "_resolve_creds", lambda: _FAKE_CREDS)
    monkeypatch.setattr(diagnostics, "_make_probes", lambda _store: _FakeProbes())
    resp = await client.post(
        "/api/diagnostics/runs", json={"checks": ["bogus"], "config": {}}
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tests: run lifecycle
# ---------------------------------------------------------------------------


async def test_run_completes_and_polls(client, monkeypatch):
    from voicegateway.server.api.dashboard import diagnostics

    monkeypatch.setattr(diagnostics, "_resolve_creds", lambda: _FAKE_CREDS)
    monkeypatch.setattr(diagnostics, "_make_probes", lambda _store: _FakeProbes())

    resp = await client.post(
        "/api/diagnostics/runs", json={"checks": ["agents"], "config": {}}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "run_id" in body
    assert body["status"] == "queued"

    run_id = body["run_id"]
    data = await _poll_until_done(client, run_id)

    assert data["status"] == "done"
    assert data["verdict"] == "PASS"
    assert "agents" in data["results"]["checks"]
    assert data["results"]["checks"]["agents"]["ok"] is True


async def test_run_conflict_when_active(client, monkeypatch):
    from voicegateway.server.api.dashboard import diagnostics

    monkeypatch.setattr(diagnostics, "_resolve_creds", lambda: _FAKE_CREDS)
    monkeypatch.setattr(diagnostics, "_make_probes", lambda _store: _SlowProbes())

    resp1 = await client.post(
        "/api/diagnostics/runs", json={"checks": ["agents"], "config": {}}
    )
    assert resp1.status_code == 200
    run_id = resp1.json()["run_id"]

    # The run should still be queued or running, so the second POST must 409.
    resp2 = await client.post(
        "/api/diagnostics/runs", json={"checks": ["agents"], "config": {}}
    )
    assert resp2.status_code == 409
    assert "already in progress" in resp2.json()["detail"]

    # Clean up: wait for the first run to finish.
    await _poll_until_done(client, run_id)


async def test_run_404_for_unknown_id(client):
    resp = await client.get("/api/diagnostics/runs/doesnotexist")
    assert resp.status_code == 404


async def test_runs_list_newest_first_and_capped(client, monkeypatch):
    from voicegateway.server.api.dashboard import diagnostics

    monkeypatch.setattr(diagnostics, "_resolve_creds", lambda: _FAKE_CREDS)
    monkeypatch.setattr(diagnostics, "_make_probes", lambda _store: _FakeProbes())

    run_ids = []
    for _ in range(5):
        resp = await client.post(
            "/api/diagnostics/runs", json={"checks": ["agents"], "config": {}}
        )
        assert resp.status_code == 200
        run_id = resp.json()["run_id"]
        run_ids.append(run_id)
        # Wait for each run to complete before starting the next.
        await _poll_until_done(client, run_id)

    resp = await client.get("/api/diagnostics/runs")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) <= 20
    # Newest first: last posted run should appear first in the list.
    assert rows[0]["run_id"] == run_ids[-1]


async def test_run_isolates_failing_check(client, monkeypatch):
    from voicegateway.server.api.dashboard import diagnostics

    monkeypatch.setattr(diagnostics, "_resolve_creds", lambda: _FAKE_CREDS)
    monkeypatch.setattr(
        diagnostics, "_make_probes", lambda _store: _FailLatencyProbes()
    )

    resp = await client.post(
        "/api/diagnostics/runs",
        json={"checks": ["agents", "latency"], "config": {}},
    )
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]

    data = await _poll_until_done(client, run_id)
    checks = data["results"]["checks"]
    assert checks["agents"]["ok"] is True
    assert checks["latency"]["ok"] is False
    assert data["verdict"] == "FAIL"


# ---------------------------------------------------------------------------
# Auth: the diagnostics endpoints are admin-gated once auth is enabled
# ---------------------------------------------------------------------------


async def test_endpoints_require_admin_when_auth_enabled(gateway, monkeypatch):
    """With static keys configured, diagnostics rejects unauthenticated callers.

    require_scope(ADMIN_SCOPE) is a no-op when no keys are configured (the local
    OSS default, exercised by every other test here). Once keys exist it enforces
    the admin scope, so an unauthenticated caller cannot trigger a billed run or
    read the configured server URL.
    """
    from voicegateway.core.auth import ADMIN_SCOPE, ApiKey
    from voicegateway.server.api.dashboard import diagnostics

    monkeypatch.setattr(diagnostics, "_resolve_creds", lambda: _FAKE_CREDS)

    app = build_app(gateway, enable_mcp_sse=False, enable_dashboard=True)
    # A non-vk_ token takes the static-key path (check_request, which reads
    # app.state.api_keys). A vk_ token would take the DB storage path instead.
    app.state.api_keys = [
        ApiKey(token="admin-secret-token", name="ops", scopes=(ADMIN_SCOPE,))
    ]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # No credentials: 401 on every endpoint, including the mutating POST.
        assert (await c.get("/api/diagnostics/creds")).status_code == 401
        assert (await c.get("/api/diagnostics/runs")).status_code == 401
        blocked = await c.post(
            "/api/diagnostics/runs", json={"checks": ["agents"], "config": {}}
        )
        assert blocked.status_code == 401

        # A valid admin token passes the gate (no run is started by GET /creds).
        ok = await c.get(
            "/api/diagnostics/creds",
            headers={"Authorization": "Bearer admin-secret-token"},
        )
        assert ok.status_code == 200
        assert ok.json() == {"configured": True, "url": "wss://x"}
