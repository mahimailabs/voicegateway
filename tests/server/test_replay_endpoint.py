"""Tests for the four v0.3.0 replay endpoints on dashboard/api/main.py (T10)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.middleware.replay_capture import ReplayEvent
from voicegateway.storage import replay_repo


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "replay-endpoint.db"))
    return Gateway(config_path=temp_config)


@pytest.fixture
async def client(gateway):
    import dashboard.api.main as dash_module

    dash_module._gateway = gateway
    transport = ASGITransport(app=dash_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    dash_module._gateway = None


async def _seed_replay(gateway, session_id: str, n: int = 3) -> None:
    db = await gateway.storage._ensure_initialized()
    try:
        events = [
            ReplayEvent(
                session_id=session_id,
                modality="stt",
                t_ms=i * 100,
                payload={"text": f"chunk-{i}", "is_final": True},
                provider="deepgram",
                cost_usd=0.0001,
            )
            for i in range(n)
        ]
        await replay_repo.bulk_write_events(db, events)
    finally:
        await db.close()


async def test_get_session_replay_returns_ordered_events(client, gateway) -> None:
    await _seed_replay(gateway, "s1", n=3)
    resp = await client.get("/api/sessions/s1/replay")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "s1"
    assert len(body["events"]) == 3
    assert [e["t_ms"] for e in body["events"]] == [0, 100, 200]
    assert body["events"][0]["modality"] == "stt"


async def test_get_session_replay_unknown_session_empty(client) -> None:
    resp = await client.get("/api/sessions/ghost/replay")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"session_id": "ghost", "events": []}


async def test_delete_session_replay_returns_count(client, gateway) -> None:
    await _seed_replay(gateway, "s1", n=4)
    resp = await client.delete("/api/sessions/s1/replay")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "s1"
    assert body["deleted_rows"] == 4

    # Verify cascade landed.
    resp2 = await client.get("/api/sessions/s1/replay")
    assert resp2.json()["events"] == []


async def test_replay_storage_endpoint_shape(client) -> None:
    resp = await client.get("/api/replay/storage")
    assert resp.status_code == 200
    body = resp.json()
    assert "total_replay_size_bytes" in body
    assert "by_project" in body
    assert isinstance(body["by_project"], list)


async def test_update_retention_validates_range(client) -> None:
    # Out-of-range body.
    resp = await client.post(
        "/api/projects/default/replay/retention",
        json={"retention_days": 0},
    )
    assert resp.status_code == 422

    resp = await client.post(
        "/api/projects/default/replay/retention",
        json={"retention_days": 1000},
    )
    assert resp.status_code == 422


async def test_update_retention_unknown_project_404(client) -> None:
    resp = await client.post(
        "/api/projects/no-such-project/replay/retention",
        json={"retention_days": 30},
    )
    assert resp.status_code == 404
