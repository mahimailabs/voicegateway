"""/v1/agents heartbeat + roster (SQLite path, operator default).

Runs on the default SQLite path (no ClickHouse client) with no Authorization
header, which the self-hosted default treats as the operator: it passes both
``require_scope("write")`` and ``require_scope("read")`` and is not tenant
scoped. A heartbeat with a far-future ``ts`` is never stale, so the worker is
served with its reported ``busy`` status.
"""

from __future__ import annotations

import os
import tempfile

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.server import build_app


class _Harness:
    """Builds an app + Gateway over a fresh SQLite db, yields a client maker."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = self._tmp.name
        self._prev_db_path = os.environ.get("VOICEGW_DB_PATH")
        os.environ["VOICEGW_DB_PATH"] = os.path.join(tmp, "agents.db")
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


async def test_heartbeat_then_roster_shows_busy(harness) -> None:
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
        assert post.json() == {"status": "accepted"}

        resp = await c.get("/v1/agents")
    assert resp.status_code == 200, resp.text
    workers = resp.json()["workers"]
    assert len(workers) == 1
    worker = workers[0]
    assert worker["agent_id"] == "agent-a"
    assert worker["agent_name"] == "Agent A"
    assert worker["status"] == "busy"
    assert worker["active_sessions"] == 2
    assert worker["region"] == "us-east"
    # tenant_id is a server-side concern and is not echoed to the roster.
    assert "tenant_id" not in worker
