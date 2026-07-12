"""GET /v1/agents returns per-worker memory + derived memory_pct."""

from __future__ import annotations

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.repository import workers_repository as repo
from voicegateway.server import build_app

_CFG = {
    "providers": {},
    "models": {"stt": {}, "llm": {}, "tts": {}},
    "fallbacks": {"stt": [], "llm": [], "tts": []},
    "cost_tracking": {"enabled": True},
}


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "agents-mem.db"))
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    p = tmp_path / "voicegw.yaml"
    p.write_text(yaml.dump(_CFG))
    return Gateway(config_path=str(p))


async def test_roster_returns_memory_and_pct(gateway):
    await gateway.storage._ensure_initialized()
    presence = {
        "agent_id": "a1", "agent_name": "bot", "project": "default",
        "tenant_id": None, "region": None, "version": "0", "host": "h",
        "active_sessions": 0, "status": "idle", "started_at": 1000.0,
        "memory_rss_bytes": 268_435_456, "memory_total_bytes": 536_870_912,
        "ts": 9_999_999_999.0,
    }
    async with gateway.storage._conn.session() as db:
        await repo.upsert_heartbeat(db, presence)

    client = AsyncClient(transport=ASGITransport(app=build_app(gateway)), base_url="http://t")
    async with client as c:
        resp = await c.get("/v1/agents")
    assert resp.status_code == 200
    row = resp.json()["workers"][0]
    assert row["memory_rss_bytes"] == 268_435_456
    assert row["memory_total_bytes"] == 536_870_912
    assert row["memory_pct"] == 50.0
