"""Phase 2: the agent query param on the dashboard read endpoints."""

from __future__ import annotations

import time
import uuid

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.models.request_model import RequestRecord
from voicegateway.server import build_app

_CFG = {
    "providers": {"openai": {"api_key": "k"}},
    "models": {"stt": {}, "llm": {}, "tts": {}},
    "projects": {},
    "fallbacks": {"stt": [], "llm": [], "tts": []},
    "cost_tracking": {"enabled": True},
}


def _cfg(tmp_path) -> str:
    p = tmp_path / "voicegw.yaml"
    p.write_text(yaml.dump(_CFG))
    return str(p)


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    return Gateway(config_path=_cfg(tmp_path))


async def _client(gw: Gateway) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=build_app(gw)), base_url="http://test"
    )


def _rec(agent: str, cost: float) -> RequestRecord:
    return RequestRecord(
        id=str(uuid.uuid4()),
        timestamp=time.time(),
        modality="llm",
        model_id="openai/gpt-4o-mini",
        provider="openai",
        project="fleet",
        cost_usd=cost,
        ttfb_ms=100.0,
        total_latency_ms=200.0,
        agent_id=agent,
        session_id=f"vg-{agent}-{uuid.uuid4()}",
    )


async def test_api_costs_filters_by_agent(gateway):
    await gateway.storage.log_request(_rec("agent-x", 0.01))
    await gateway.storage.log_request(_rec("agent-y", 0.05))
    client = await _client(gateway)
    async with client as c:
        resp = await c.get("/api/costs?period=today&agent=agent-x")
    assert resp.status_code == 200
    assert resp.json()["total"] == pytest.approx(0.01)


async def test_api_sessions_filters_by_agent(gateway):
    await gateway.storage.log_request(_rec("agent-x", 0.01))
    await gateway.storage.log_request(_rec("agent-y", 0.05))
    client = await _client(gateway)
    async with client as c:
        resp = await c.get("/api/sessions?agent=agent-x")
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()]
    assert ids
    assert all(i.startswith("vg-agent-x") for i in ids)


async def test_api_agents_index(gateway):
    await gateway.storage.log_request(_rec("agent-x", 0.01))
    await gateway.storage.log_request(_rec("agent-x", 0.02))
    await gateway.storage.log_request(_rec("agent-y", 0.05))
    client = await _client(gateway)
    async with client as c:
        resp = await c.get("/api/agents")
    assert resp.status_code == 200
    body = resp.json()
    by_id = {a["agent_id"]: a for a in body["agents"]}
    assert set(by_id) == {"agent-x", "agent-y"}
    assert by_id["agent-x"]["request_count"] == 2
    assert "p95_latency_ms" in by_id["agent-x"]
    assert "unattributed" in body


async def test_api_logs_filters_by_agent(gateway):
    await gateway.storage.log_request(_rec("agent-x", 0.01))
    await gateway.storage.log_request(_rec("agent-y", 0.05))
    client = await _client(gateway)
    async with client as c:
        resp = await c.get("/api/logs?agent=agent-x")
    assert resp.status_code == 200
    rows = resp.json()
    assert rows
    assert all(r["agent_id"] == "agent-x" for r in rows)
