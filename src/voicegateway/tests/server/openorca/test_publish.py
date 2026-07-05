"""A heartbeat write publishes agent/fleet updates onto the OpenOrca bus.

Subscribing to the module-level bus before the POST, then draining the
subscription after, proves the heartbeat endpoint fans a ``fleet.updated``
(and, when the node is found, an ``agent.updated``) frame to connected
dashboards without a client poll.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.server import build_app
from voicegateway.server.api.openorca.routes import bus


class _Harness:
    """Builds an app + Gateway over a fresh SQLite db, yields a client maker."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = self._tmp.name
        self._prev_db_path = os.environ.get("VOICEGW_DB_PATH")
        os.environ["VOICEGW_DB_PATH"] = os.path.join(tmp, "openorca_pub.db")
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


async def test_heartbeat_publishes_fleet_updated(harness) -> None:
    sub = bus.subscribe()
    presence = {
        "agent_id": "agent-a",
        "agent_name": "Agent A",
        "project": "default",
        "host": "host-1",
        "active_sessions": 1,
        "status": "busy",
        "ts": 9e12,
    }
    try:
        async with harness.client() as c:
            post = await c.post("/v1/agents/heartbeat", json=presence)
            assert post.status_code == 202, post.text

        events = []
        try:
            for _ in range(4):
                events.append(await asyncio.wait_for(sub.get(), timeout=1.0))
        except TimeoutError:
            pass
    finally:
        await sub.aclose()

    types = [e["type"] for e in events]
    assert "fleet.updated" in types
