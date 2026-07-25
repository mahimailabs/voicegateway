"""GET /api/sessions/{id}/transcript serves stored transcript turns."""

from __future__ import annotations

import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.server import build_app


def _gateway(tmp_path, monkeypatch) -> Gateway:
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "transcript.db"))
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    path = tmp_path / "voicegw.yaml"
    path.write_text(yaml.dump({"cost_tracking": {"enabled": True}}))
    return Gateway(config_path=str(path))


def _client(gw: Gateway) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=build_app(gw)), base_url="http://test"
    )


async def test_transcript_endpoint_returns_ordered_turns(tmp_path, monkeypatch):
    gw = _gateway(tmp_path, monkeypatch)
    await gw.storage.write_transcript(
        "call-1", [("user", "hi"), ("agent", "hello there")]
    )
    async with _client(gw) as c:
        data = (await c.get("/api/sessions/call-1/transcript")).json()
    assert data["session_id"] == "call-1"
    assert [(t["role"], t["text"]) for t in data["turns"]] == [
        ("user", "hi"),
        ("agent", "hello there"),
    ]


async def test_transcript_endpoint_empty_for_unknown_call(tmp_path, monkeypatch):
    gw = _gateway(tmp_path, monkeypatch)
    async with _client(gw) as c:
        data = (await c.get("/api/sessions/nope/transcript")).json()
    assert data == {"session_id": "nope", "turns": []}
