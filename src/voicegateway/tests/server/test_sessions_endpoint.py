"""Tests for /v1/sessions and /v1/sessions/{id} HTTP endpoints."""

from __future__ import annotations

import time
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.models.request_model import RequestRecord
from voicegateway.server import build_app


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
    storage,
    sid: str,
    *,
    project: str = "tony-pizza",
    modality: str = "stt",
    cost: float = 0.001,
    ts: float | None = None,
    provider: str = "deepgram",
):
    rec = RequestRecord(
        id=str(uuid.uuid4()),
        timestamp=ts if ts is not None else time.time(),
        modality=modality,
        model_id=f"{provider}/{modality}-test",
        provider=provider,
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
    """The list endpoint returns modalities as a JSON array (not the"""
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
    # AC-002.1: detail responses carry per-modality breakdown +
    # providers list. A single-modality session has one entry.
    assert "by_modality" in data
    assert data["by_modality"]["stt"]["request_count"] == 1
    assert abs(data["by_modality"]["stt"]["cost"] - 0.05) < 1e-9
    assert data["providers"] == ["deepgram"]


async def test_session_detail_per_modality_breakdown_aggregates_by_modality(
    client, gateway
):
    """A session with mixed STT / LLM / TTS requests returns one"""
    await _seed_session(
        gateway.storage, "vg-mix", modality="stt", cost=0.01, provider="deepgram"
    )
    await _seed_session(
        gateway.storage, "vg-mix", modality="llm", cost=0.02, provider="openai"
    )
    await _seed_session(
        gateway.storage, "vg-mix", modality="llm", cost=0.03, provider="openai"
    )
    await _seed_session(
        gateway.storage, "vg-mix", modality="tts", cost=0.04, provider="cartesia"
    )

    resp = await client.get("/v1/sessions/vg-mix")
    data = resp.json()

    by_mod = data["by_modality"]
    assert set(by_mod.keys()) == {"stt", "llm", "tts"}
    assert abs(by_mod["stt"]["cost"] - 0.01) < 1e-9
    assert by_mod["stt"]["request_count"] == 1
    assert abs(by_mod["llm"]["cost"] - 0.05) < 1e-9
    assert by_mod["llm"]["request_count"] == 2
    assert abs(by_mod["tts"]["cost"] - 0.04) < 1e-9
    assert by_mod["tts"]["request_count"] == 1

    # Providers list deduplicates and sorts.
    assert data["providers"] == ["cartesia", "deepgram", "openai"]


async def test_session_detail_ended_at_advances_with_each_request(client, gateway):
    """AC-002.3: ended_at tracks last-activity, not first-activity, so"""
    await _seed_session(gateway.storage, "vg-dur", ts=1700000000.0, cost=0.01)
    await _seed_session(gateway.storage, "vg-dur", ts=1700000050.0, cost=0.01)
    await _seed_session(gateway.storage, "vg-dur", ts=1700000123.0, cost=0.01)

    resp = await client.get("/v1/sessions/vg-dur")
    data = resp.json()
    assert data["started_at"] != data["ended_at"]
    # Both are ISO 8601 — string comparison reflects time order.
    assert data["ended_at"] > data["started_at"]


async def test_session_detail_out_of_order_request_does_not_drag_ended_at_back(
    client, gateway
):
    """A late-arriving record with an older timestamp must NOT move"""
    await _seed_session(gateway.storage, "vg-ooo", ts=1700000100.0)
    expected_ended = (await gateway.storage.get_session("vg-ooo"))["ended_at"]
    await _seed_session(gateway.storage, "vg-ooo", ts=1700000050.0)  # earlier!

    resp = await client.get("/v1/sessions/vg-ooo")
    data = resp.json()
    assert data["ended_at"] == expected_ended  # unchanged


# ---------------------------------------------------------------------------
# /v1/sessions ordering (AC-002.3)
# ---------------------------------------------------------------------------


async def test_list_sessions_order_by_cost_desc(client, gateway):
    await _seed_session(gateway.storage, "vg-cheap", cost=0.001, ts=1700000300.0)
    await _seed_session(gateway.storage, "vg-mid", cost=0.05, ts=1700000200.0)
    await _seed_session(gateway.storage, "vg-pricey", cost=0.50, ts=1700000100.0)

    resp = await client.get("/v1/sessions?order_by=cost_desc")
    rows = resp.json()
    assert [r["id"] for r in rows] == ["vg-pricey", "vg-mid", "vg-cheap"]


async def test_list_sessions_order_by_cost_asc(client, gateway):
    await _seed_session(gateway.storage, "vg-cheap", cost=0.001, ts=1700000300.0)
    await _seed_session(gateway.storage, "vg-mid", cost=0.05, ts=1700000200.0)
    await _seed_session(gateway.storage, "vg-pricey", cost=0.50, ts=1700000100.0)

    resp = await client.get("/v1/sessions?order_by=cost_asc")
    rows = resp.json()
    assert [r["id"] for r in rows] == ["vg-cheap", "vg-mid", "vg-pricey"]


async def test_list_sessions_order_by_started_at_asc(client, gateway):
    await _seed_session(gateway.storage, "vg-old", ts=1700000000.0)
    await _seed_session(gateway.storage, "vg-new", ts=1750000000.0)

    resp = await client.get("/v1/sessions?order_by=started_at_asc")
    rows = resp.json()
    assert [r["id"] for r in rows] == ["vg-old", "vg-new"]


async def test_list_sessions_default_order_is_started_at_desc(client, gateway):
    """Omitting order_by must keep the legacy newest-first behaviour."""
    await _seed_session(gateway.storage, "vg-old", ts=1700000000.0, cost=10.0)
    await _seed_session(gateway.storage, "vg-new", ts=1750000000.0, cost=0.01)

    resp = await client.get("/v1/sessions")
    rows = resp.json()
    assert [r["id"] for r in rows] == ["vg-new", "vg-old"]


async def test_list_sessions_invalid_order_by_returns_422(client):
    resp = await client.get("/v1/sessions?order_by=cost_random")
    assert resp.status_code == 422


async def test_session_detail_returns_404_for_missing(client):
    resp = await client.get("/v1/sessions/does-not-exist")
    assert resp.status_code == 404
    detail = resp.json()
    assert "does-not-exist" in detail["detail"]


async def test_session_detail_returns_404_when_storage_disabled(
    temp_config, tmp_path, monkeypatch
):
    """If cost_tracking is disabled (no storage), the endpoint cannot"""
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
