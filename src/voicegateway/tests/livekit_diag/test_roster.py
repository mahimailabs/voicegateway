"""fetch_roster: GET the collector's worker roster with the vk_ key, best-effort.

The engine's heartbeat writes the roster; this reads it back for the CLI. Any
error (unset collector, network, auth, non-list body) yields [] so
``voicegw livekit agents`` still renders the in-room view.
"""

from __future__ import annotations

import httpx

from voicegateway.livekit_diag.roster import fetch_roster

# Capture the real class up front: the tests monkeypatch httpx.AsyncClient, so
# the factory must not route back through the patched symbol (that recurses).
_RealAsyncClient = httpx.AsyncClient


def _client(handler) -> httpx.AsyncClient:
    return _RealAsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_roster_returns_list_and_sends_bearer(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=[{"agent_name": "realty", "status": "idle"}])

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _client(handler))
    out = await fetch_roster("https://collector.test/", "vk_secret")
    assert out == [{"agent_name": "realty", "status": "idle"}]
    assert seen["url"] == "https://collector.test/v1/agents"  # trailing slash trimmed
    assert seen["auth"] == "Bearer vk_secret"


async def test_fetch_roster_no_api_key_sends_no_auth_header(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=[])

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _client(handler))
    out = await fetch_roster("https://collector.test", None)
    assert out == []
    assert seen["auth"] is None


async def test_fetch_roster_swallows_http_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "nope"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _client(handler))
    assert await fetch_roster("https://collector.test", "vk_bad") == []


async def test_fetch_roster_non_list_body_is_empty(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _client(handler))
    assert await fetch_roster("https://collector.test", "vk_x") == []
