"""Tests for POST /v1/ingest (fleet collector ingest endpoint)."""

from __future__ import annotations

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.repository import virtual_keys_repository as virtual_keys
from voicegateway.server import build_app

_BASE_CONFIG = {
    "providers": {"openai": {"api_key": "test-key"}},
    "models": {"stt": {}, "llm": {}, "tts": {}},
    "projects": {},
    "fallbacks": {"stt": [], "llm": [], "tts": []},
    "cost_tracking": {"enabled": True},
}


def _write_config(tmp_path):
    path = tmp_path / "voicegw.yaml"
    with open(path, "w") as f:
        yaml.dump(_BASE_CONFIG, f)
    return str(path)


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "ingest.db"))
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    return Gateway(config_path=_write_config(tmp_path))


async def _client(gw: Gateway):
    app = build_app(gw)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _payload(rid: str, agent_id: str = "agent-1") -> dict:
    return {
        "id": rid,
        "timestamp": 1_000_000.0,
        "modality": "llm",
        "model_id": "openai/gpt-4o-mini",
        "provider": "openai",
        "project": "fleet",
        "input_units": 100.0,
        "output_units": 50.0,
        "cost_usd": 0.001,
        "agent_id": agent_id,
    }


async def test_ingest_accepts_batch_and_stamps_tenant(gateway):
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        created = await virtual_keys.create_virtual_key(
            db, name="bot", tenant_id="acme"
        )

    client = await _client(gateway)
    async with client as c:
        resp = await c.post(
            "/v1/ingest",
            headers={"Authorization": f"Bearer {created.plaintext}"},
            json=[_payload("ing-1", "agent-a"), _payload("ing-2", "agent-b")],
        )

    assert resp.status_code == 200
    assert resp.json() == {"accepted": 2, "duplicates": 0}

    rows = await gateway.storage.get_recent_requests(limit=10)
    assert len(rows) == 2
    assert {r["agent_id"] for r in rows} == {"agent-a", "agent-b"}
    # tenant stamped server-side from the verified key, not the payload.
    assert all(r["tenant_id"] == "acme" for r in rows)


async def test_ingest_is_idempotent_on_id(gateway):
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        created = await virtual_keys.create_virtual_key(db, name="bot")

    client = await _client(gateway)
    async with client as c:
        first = await c.post(
            "/v1/ingest",
            headers={"Authorization": f"Bearer {created.plaintext}"},
            json=[_payload("dup-1")],
        )
        second = await c.post(
            "/v1/ingest",
            headers={"Authorization": f"Bearer {created.plaintext}"},
            json=[_payload("dup-1")],
        )

    assert first.json() == {"accepted": 1, "duplicates": 0}
    assert second.json() == {"accepted": 0, "duplicates": 1}

    rows = await gateway.storage.get_recent_requests(limit=10)
    assert len([r for r in rows if r["id"] == "dup-1"]) == 1


async def test_ingest_rejects_revoked_key(gateway):
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        created = await virtual_keys.create_virtual_key(db, name="bot")
        await virtual_keys.revoke(db, created.id)

    client = await _client(gateway)
    async with client as c:
        resp = await c.post(
            "/v1/ingest",
            headers={"Authorization": f"Bearer {created.plaintext}"},
            json=[_payload("x")],
        )
    assert resp.status_code == 401
