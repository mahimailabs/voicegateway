"""Phase 3: rate limiting (429), batch cap (413), storage-off (503) on /v1/ingest."""

from __future__ import annotations

import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.repository import virtual_keys_repository as virtual_keys
from voicegateway.server import build_app


def _gateway(tmp_path, monkeypatch, config: dict, *, db: bool = True) -> Gateway:
    if db:
        monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "rl.db"))
    else:
        # Storage is enabled by env-var OR cost_tracking.enabled; clear the env
        # var so a cost_tracking-disabled config truly yields storage=None.
        monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    path = tmp_path / "voicegw.yaml"
    path.write_text(yaml.dump(config))
    return Gateway(config_path=str(path))


def _client(gw: Gateway) -> AsyncClient:
    transport = ASGITransport(app=build_app(gw))
    return AsyncClient(transport=transport, base_url="http://test")


def _payload(rid: str) -> dict:
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
        "agent_id": "a",
    }


async def _vk(gw: Gateway) -> str:
    await gw.storage._ensure_initialized()
    async with gw.storage._conn.session() as db:
        created = await virtual_keys.create_virtual_key(db, name="bot")
    return created.plaintext


async def test_ingest_429_when_over_rate(tmp_path, monkeypatch) -> None:
    gw = _gateway(
        tmp_path,
        monkeypatch,
        {
            "cost_tracking": {"enabled": True},
            "ingest": {"requests_per_minute": 1, "burst": 1},
        },
    )
    headers = {"Authorization": f"Bearer {await _vk(gw)}"}
    async with _client(gw) as c:
        first = await c.post("/v1/ingest", headers=headers, json=[_payload("r1")])
        second = await c.post("/v1/ingest", headers=headers, json=[_payload("r2")])
    assert first.status_code == 200
    assert second.status_code == 429
    assert int(second.headers["retry-after"]) >= 1


async def test_ingest_413_when_batch_too_large(tmp_path, monkeypatch) -> None:
    gw = _gateway(
        tmp_path,
        monkeypatch,
        {"cost_tracking": {"enabled": True}, "ingest": {"max_batch_size": 2}},
    )
    headers = {"Authorization": f"Bearer {await _vk(gw)}"}
    async with _client(gw) as c:
        resp = await c.post(
            "/v1/ingest",
            headers=headers,
            json=[_payload("a"), _payload("b"), _payload("c")],
        )
    assert resp.status_code == 413


async def test_ingest_429_precedes_413(tmp_path, monkeypatch) -> None:
    gw = _gateway(
        tmp_path,
        monkeypatch,
        {
            "cost_tracking": {"enabled": True},
            "ingest": {"requests_per_minute": 1, "burst": 1, "max_batch_size": 2},
        },
    )
    headers = {"Authorization": f"Bearer {await _vk(gw)}"}
    async with _client(gw) as c:
        first = await c.post("/v1/ingest", headers=headers, json=[_payload("r1")])
        # The second request is both over-rate and oversized; 429 must win.
        second = await c.post(
            "/v1/ingest",
            headers=headers,
            json=[_payload("a"), _payload("b"), _payload("c")],
        )
    assert first.status_code == 200
    assert second.status_code == 429


async def test_ingest_503_when_storage_disabled(tmp_path, monkeypatch) -> None:
    gw = _gateway(
        tmp_path,
        monkeypatch,
        {
            "cost_tracking": {"enabled": False},
            "auth": {"api_keys": [{"token": "static-key", "scopes": ["*"]}]},
        },
        db=False,
    )
    headers = {"Authorization": "Bearer static-key"}
    async with _client(gw) as c:
        resp = await c.post("/v1/ingest", headers=headers, json=[_payload("x")])
    assert resp.status_code == 503
