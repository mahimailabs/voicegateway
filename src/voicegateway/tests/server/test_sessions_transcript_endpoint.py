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


# ---------------------------------------------------------------------------
# Auth: the transcript is the most sensitive payload on the router
# ---------------------------------------------------------------------------


async def test_transcript_stays_open_when_no_keys_are_configured(tmp_path, monkeypatch):
    """The self-hosted default (no keys configured) is unchanged."""
    gw = _gateway(tmp_path, monkeypatch)
    app = build_app(gw)
    assert app.state.api_keys == []
    await gw.storage.write_transcript("call-open", [("user", "hi")])

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.get("/api/sessions/call-open/transcript")
    assert resp.status_code == 200
    assert [t["text"] for t in resp.json()["turns"]] == ["hi"]


async def test_transcript_requires_auth_when_enabled(tmp_path, monkeypatch):
    """With static keys configured, a session id alone does not buy a call.

    This route was ungated: anyone who could reach the port could read what
    the caller said, on any session, by guessing or scraping one id.
    """
    from voicegateway.core.auth import ApiKey

    gw = _gateway(tmp_path, monkeypatch)
    app = build_app(gw)
    app.state.api_keys = [ApiKey(token="read-token", name="viewer", scopes=("read",))]
    await gw.storage.write_transcript("call-secret", [("user", "my card number is")])

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        anon = await c.get("/api/sessions/call-secret/transcript")
        wrong = await c.get(
            "/api/sessions/call-secret/transcript",
            headers={"Authorization": "Bearer nope"},
        )
        ok = await c.get(
            "/api/sessions/call-secret/transcript",
            headers={"Authorization": "Bearer read-token"},
        )

    assert anon.status_code == 401, anon.text
    assert "my card number is" not in anon.text
    assert wrong.status_code == 401
    assert ok.status_code == 200
    assert [t["text"] for t in ok.json()["turns"]] == ["my card number is"]
