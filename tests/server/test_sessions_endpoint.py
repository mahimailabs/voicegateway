"""Tests for /v1/sessions and /v1/sessions/{id} HTTP endpoints."""

from __future__ import annotations

import time
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.server import build_app
from voicegateway.storage.models import RequestRecord


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "sessions-endpoint.db"))
    return Gateway(config_path=temp_config)


@pytest.fixture
def app(gateway):
    return build_app(gateway)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_session(
    storage, sid: str, *, project: str = "tony-pizza", modality: str = "stt",
    cost: float = 0.001, ts: float | None = None,
):
    rec = RequestRecord(
        id=str(uuid.uuid4()),
        timestamp=ts if ts is not None else time.time(),
        modality=modality,
        model_id=f"deepgram/{modality}-test",
        provider="deepgram",
        project=project,
        cost_usd=cost,
        session_id=sid,
    )
    await storage.log_request(rec)


# ---------------------------------------------------------------------------
# /v1/sessions (list)
# ---------------------------------------------------------------------------


async def test_list_sessions_empty(client):
    resp = await client.get("/v1/sessions")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_sessions_returns_seeded_rows(client, gateway):
    await _seed_session(gateway.storage, "vg-a", project="tony-pizza", cost=0.01)
    await _seed_session(gateway.storage, "vg-b", project="mama-diner", cost=0.02)

    resp = await client.get("/v1/sessions")
    assert resp.status_code == 200
    rows = resp.json()
    ids = {r["id"] for r in rows}
    assert ids == {"vg-a", "vg-b"}


async def test_list_sessions_returns_modalities_as_list(client, gateway):
    """The list endpoint returns modalities as a JSON array (not the
    raw comma-separated string in the table) so dashboard consumers
    don't have to split client-side.
    """
    await _seed_session(gateway.storage, "vg-multi", modality="stt", cost=0.01)
    await _seed_session(gateway.storage, "vg-multi", modality="llm", cost=0.02)
    await _seed_session(gateway.storage, "vg-multi", modality="tts", cost=0.03)

    resp = await client.get("/v1/sessions")
    rows = resp.json()
    row = next(r for r in rows if r["id"] == "vg-multi")
    assert isinstance(row["modalities"], list)
    assert sorted(row["modalities"]) == ["llm", "stt", "tts"]
    assert row["request_count"] == 3
    assert abs(row["total_cost_usd"] - 0.06) < 1e-9


async def test_list_sessions_orders_newest_first(client, gateway):
    # Seed two sessions with deterministic timestamps.
    await _seed_session(gateway.storage, "vg-old", ts=1700000000.0)
    await _seed_session(gateway.storage, "vg-new", ts=1750000000.0)

    resp = await client.get("/v1/sessions")
    rows = resp.json()
    assert [r["id"] for r in rows] == ["vg-new", "vg-old"]


async def test_list_sessions_limit_parameter(client, gateway):
    for i in range(5):
        await _seed_session(gateway.storage, f"vg-{i}", ts=1700000000.0 + i)

    resp = await client.get("/v1/sessions?limit=2")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2


async def test_list_sessions_project_filter(client, gateway):
    await _seed_session(gateway.storage, "vg-a", project="tony-pizza")
    await _seed_session(gateway.storage, "vg-b", project="mama-diner")
    await _seed_session(gateway.storage, "vg-c", project="tony-pizza")

    resp = await client.get("/v1/sessions?project=tony-pizza")
    rows = resp.json()
    ids = sorted(r["id"] for r in rows)
    assert ids == ["vg-a", "vg-c"]


async def test_list_sessions_limit_validation(client):
    """Out-of-range limit returns 422 (FastAPI Query bounds)."""
    resp = await client.get("/v1/sessions?limit=0")
    assert resp.status_code == 422

    resp = await client.get("/v1/sessions?limit=10000")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /v1/sessions/{id} (detail)
# ---------------------------------------------------------------------------


async def test_session_detail_returns_session(client, gateway):
    await _seed_session(gateway.storage, "vg-detail", project="default", cost=0.05)

    resp = await client.get("/v1/sessions/vg-detail")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "vg-detail"
    assert data["project"] == "default"
    assert abs(data["total_cost_usd"] - 0.05) < 1e-9
    assert data["request_count"] == 1
    assert isinstance(data["modalities"], list)


async def test_session_detail_returns_404_for_missing(client):
    resp = await client.get("/v1/sessions/does-not-exist")
    assert resp.status_code == 404
    detail = resp.json()
    assert "does-not-exist" in detail["detail"]


async def test_session_detail_returns_404_when_storage_disabled(
    temp_config, tmp_path, monkeypatch
):
    """If cost_tracking is disabled (no storage), the endpoint cannot
    return any session by id and must 404.
    """
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    # Build with no DB path and storage disabled by config default; the
    # temp_config fixture has cost_tracking.enabled=True, so override
    # by writing a fresh config without it.
    import yaml as _yaml

    cfg_path = tmp_path / "no-storage.yaml"
    cfg_path.write_text(
        _yaml.dump(
            {
                "providers": {"openai": {"api_key": "test"}},
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "stacks": {},
                "fallbacks": {"stt": [], "llm": [], "tts": []},
                "cost_tracking": {"enabled": False},
                "observability": {"latency_tracking": True},
            }
        )
    )
    gw = Gateway(config_path=str(cfg_path))
    assert gw.storage is None
    app = build_app(gw)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/v1/sessions/anything")
        assert resp.status_code == 404


async def test_list_sessions_returns_empty_when_storage_disabled(
    temp_config, tmp_path, monkeypatch
):
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    import yaml as _yaml

    cfg_path = tmp_path / "no-storage.yaml"
    cfg_path.write_text(
        _yaml.dump(
            {
                "providers": {"openai": {"api_key": "test"}},
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "stacks": {},
                "fallbacks": {"stt": [], "llm": [], "tts": []},
                "cost_tracking": {"enabled": False},
                "observability": {"latency_tracking": True},
            }
        )
    )
    gw = Gateway(config_path=str(cfg_path))
    assert gw.storage is None
    app = build_app(gw)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/v1/sessions")
        assert resp.status_code == 200
        assert resp.json() == []
