"""Phase 3, Step 7: GET /api/agents LIST serves the windowed rollup.

The LIST endpoint reads agent_observations (not a live requests scan); error_rate
is derived from error_count / request_count; the unattributed bucket is windowed.
The DETAIL endpoint stays all-time (reads requests).
"""

from __future__ import annotations

import yaml
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from voicegateway.core.gateway import Gateway
from voicegateway.models.request_model import RequestRecord
from voicegateway.server import build_app


def _gateway(tmp_path, monkeypatch) -> Gateway:
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "agents.db"))
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    path = tmp_path / "voicegw.yaml"
    path.write_text(yaml.dump({"cost_tracking": {"enabled": True}}))
    return Gateway(config_path=str(path))


def _client(gw: Gateway) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=build_app(gw)), base_url="http://test"
    )


async def _insert_obs(gw: Gateway, **cols) -> None:
    await gw.storage._ensure_initialized()
    keys = ", ".join(cols)
    vals = ", ".join(f":{k}" for k in cols)
    async with gw.storage._conn.session() as db:
        await db.execute(
            text(f"INSERT INTO agent_observations ({keys}) VALUES ({vals})"), cols
        )
        await db.commit()


async def test_list_reads_from_rollup_not_requests(tmp_path, monkeypatch) -> None:
    gw = _gateway(tmp_path, monkeypatch)
    # A rollup row with NO matching requests rows: the LIST can only return it
    # if it reads the rollup table rather than scanning requests.
    await _insert_obs(
        gw,
        agent_id="ghost",
        request_count=7,
        total_cost_usd=0.5,
        error_count=0,
        p95_ms=300,
        last_seen=1000.0,
        window_start="ws",
        window_end="we",
    )
    async with _client(gw) as c:
        resp = await c.get("/api/agents")
    assert resp.status_code == 200
    ghost = [a for a in resp.json()["agents"] if a["agent_id"] == "ghost"]
    assert len(ghost) == 1
    assert ghost[0]["request_count"] == 7
    assert ghost[0]["p95_latency_ms"] == 300


async def test_list_derives_error_rate(tmp_path, monkeypatch) -> None:
    gw = _gateway(tmp_path, monkeypatch)
    await _insert_obs(
        gw,
        agent_id="a",
        request_count=4,
        total_cost_usd=0.0,
        error_count=1,
        last_seen=1.0,
        window_start="ws",
        window_end="we",
    )
    async with _client(gw) as c:
        data = (await c.get("/api/agents")).json()
    entry = next(x for x in data["agents"] if x["agent_id"] == "a")
    assert entry["error_rate"] == 0.25


async def test_list_unattributed_is_windowed(tmp_path, monkeypatch) -> None:
    gw = _gateway(tmp_path, monkeypatch)
    await _insert_obs(
        gw,
        agent_id=None,
        request_count=3,
        total_cost_usd=0.1,
        error_count=0,
        last_seen=1.0,
        window_start="ws",
        window_end="we",
    )
    async with _client(gw) as c:
        data = (await c.get("/api/agents")).json()
    assert data["unattributed"]["request_count"] == 3


async def test_detail_is_all_time_from_requests(tmp_path, monkeypatch) -> None:
    gw = _gateway(tmp_path, monkeypatch)
    for i in range(2):
        await gw.storage.log_request(
            RequestRecord(
                id=f"r{i}",
                timestamp=1000.0 + i,
                modality="llm",
                model_id="openai/gpt-4o-mini",
                provider="openai",
                project="default",
                agent_id="agent-a",
            )
        )
    # A divergent rollup row the DETAIL endpoint must ignore (it is all-time).
    await _insert_obs(
        gw,
        agent_id="agent-a",
        request_count=99,
        total_cost_usd=0.0,
        error_count=0,
        last_seen=1.0,
        window_start="ws",
        window_end="we",
    )
    async with _client(gw) as c:
        detail = (await c.get("/api/agents/agent-a")).json()
    assert detail["request_count"] == 2  # from requests, not the rollup's 99


async def _insert_worker(gw: Gateway, **cols) -> None:
    await gw.storage._ensure_initialized()
    keys = ", ".join(cols)
    vals = ", ".join(f":{k}" for k in cols)
    async with gw.storage._conn.session() as db:
        await db.execute(
            text(f"INSERT INTO workers ({keys}) VALUES ({vals})"), cols
        )
        await db.commit()


async def test_list_merges_worker_memory_pct(tmp_path, monkeypatch) -> None:
    gw = _gateway(tmp_path, monkeypatch)
    await _insert_obs(
        gw,
        agent_id="mem-agent",
        request_count=1,
        total_cost_usd=0.0,
        error_count=0,
        last_seen=1000.0,
        window_start="ws",
        window_end="we",
    )
    # A live worker row: RSS 1 GiB of a 4 GiB ceiling -> 25.0%.
    await _insert_worker(
        gw,
        agent_id="mem-agent",
        agent_name="mem-agent",
        last_seen=1000.0,
        memory_rss_bytes=1073741824,
        memory_total_bytes=4294967296,
    )
    async with _client(gw) as c:
        data = (await c.get("/api/agents")).json()
    entry = next(x for x in data["agents"] if x["agent_id"] == "mem-agent")
    assert entry["memory_pct"] == 25.0


async def test_list_memory_pct_null_when_no_worker(tmp_path, monkeypatch) -> None:
    gw = _gateway(tmp_path, monkeypatch)
    # An agent seen in telemetry but with no heartbeating worker row: the field
    # is present and null, never absent.
    await _insert_obs(
        gw,
        agent_id="rollup-only",
        request_count=1,
        total_cost_usd=0.0,
        error_count=0,
        last_seen=1000.0,
        window_start="ws",
        window_end="we",
    )
    async with _client(gw) as c:
        data = (await c.get("/api/agents")).json()
    entry = next(x for x in data["agents"] if x["agent_id"] == "rollup-only")
    assert entry["memory_pct"] is None
