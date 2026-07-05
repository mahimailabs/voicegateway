"""OpenOrca dashboard endpoints (SQLite path, operator default).

Runs on the default SQLite path (no ClickHouse client) with no Authorization
header, which the self-hosted default treats as the operator: it passes
require_principal and resolves to the all-tenants read. A heartbeat with a
far-future ``ts`` is never stale, so the mapped node is served active. The full
streaming HTTP path is deliberately not exercised here (see test_bus for the
bus and the unit test below for the SSE frame); this keeps the suite flake-free.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.server import build_app
from voicegateway.server.api.openorca.routes import _sse


class _Harness:
    """Builds an app + Gateway over a fresh SQLite db, yields a client maker."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = self._tmp.name
        self._prev_db_path = os.environ.get("VOICEGW_DB_PATH")
        os.environ["VOICEGW_DB_PATH"] = os.path.join(tmp, "openorca.db")
        cfg = {
            "providers": {"openai": {"api_key": "test-key"}},
            "models": {"stt": {}, "llm": {}, "tts": {}},
            "projects": {},
            "fallbacks": {"stt": [], "llm": [], "tts": []},
            "cost_tracking": {"enabled": True},
        }
        cfg_path = os.path.join(tmp, "voicegw.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(cfg, f)
        self.gateway = Gateway(config_path=cfg_path)
        self.app = build_app(self.gateway, enable_mcp_sse=False, enable_dashboard=False)
        self.app.state.ch_client = None

    def client(self) -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=self.app), base_url="http://test"
        )

    def cleanup(self) -> None:
        if self._prev_db_path is None:
            os.environ.pop("VOICEGW_DB_PATH", None)
        else:
            os.environ["VOICEGW_DB_PATH"] = self._prev_db_path
        self._tmp.cleanup()


@pytest.fixture
async def harness():
    h = _Harness()
    await h.gateway.storage._ensure_initialized()
    try:
        yield h
    finally:
        h.cleanup()


async def test_heartbeat_then_snapshot_shows_node(harness) -> None:
    presence = {
        "agent_id": "agent-a",
        "agent_name": "Agent A",
        "project": "default",
        "region": "us-east",
        "version": "1.2.3",
        "host": "host-1",
        "active_sessions": 2,
        "status": "busy",
        "started_at": 100.0,
        "ts": 9e12,  # far future -> never stale
    }
    async with harness.client() as c:
        post = await c.post("/v1/agents/heartbeat", json=presence)
        assert post.status_code == 202, post.text

        resp = await c.get("/openorca/snapshot")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["meta"]["runtime"] == "voicegateway"
    assert len(body["agents"]) == 1
    assert body["agents"][0]["id"] == "Agent A"
    assert body["agents"][0]["status"] == "active"
    assert body["fleetHealth"]["activeAgents"] == 1


async def test_resolve_intervention_passes_operator_default(harness) -> None:
    """The resolve write is auth-gated but the no-credential operator passes."""
    async with harness.client() as c:
        resp = await c.post(
            "/openorca/interventions/resolve",
            json={"interventionId": "iv-1", "action": "approve"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "resolved"}


async def test_runtime_info_advertises_sse(harness) -> None:
    async with harness.client() as c:
        resp = await c.get("/openorca/runtime-info")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["runtime"] == "voicegateway"
    assert body["supports"]["sse"] is True


def test_sse_frame_formatting() -> None:
    frame = _sse({"type": "fleet.updated"})
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")
    assert json.loads(frame[len("data: ") :].strip()) == {"type": "fleet.updated"}
