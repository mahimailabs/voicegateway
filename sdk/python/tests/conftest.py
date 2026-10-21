"""A local aiohttp server that stands in for VoiceGateway and records every batch."""

from __future__ import annotations

import gzip
import json
import socket
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from jsonschema import Draft202012Validator
from livekit import api

CONTRACT = Path(__file__).resolve().parents[2] / "contract"
KEY = "APItestkey"
SECRET = "test-secret-at-least-thirty-two-characters-long"


@pytest.fixture(scope="session")
def validator() -> Draft202012Validator:
    schema = json.loads((CONTRACT / "v1.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


@pytest.fixture
def creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEKIT_API_KEY", KEY)
    monkeypatch.setenv("LIVEKIT_API_SECRET", SECRET)
    monkeypatch.delenv("VOICEGATEWAY_URL", raising=False)


@dataclass
class Endpoint:
    url: str
    batches: list[dict[str, Any]] = field(default_factory=list)
    # Status codes to answer in order; 200 once they run out.
    statuses: list[int] = field(default_factory=list)
    attempts: int = 0

    def events(self) -> list[dict[str, Any]]:
        return [event for batch in self.batches for event in batch["events"]]


@pytest.fixture
async def endpoint() -> AsyncIterator[Endpoint]:
    ep = Endpoint(url="")
    verifier = api.TokenVerifier(KEY, SECRET)

    async def handle(request: web.Request) -> web.Response:
        ep.attempts += 1
        assert request.headers["Content-Encoding"] == "gzip"
        auth = request.headers["Authorization"]
        assert auth.startswith("Bearer ")
        claims = verifier.verify(auth.removeprefix("Bearer "))
        assert claims.observability is not None and claims.observability.write
        raw = await request.content.read()
        body = json.loads(gzip.decompress(raw))
        status = ep.statuses.pop(0) if ep.statuses else 200
        if status == 200:
            ep.batches.append(body)
        return web.Response(status=status)

    app = web.Application()
    app.router.add_post("/ingest/org1/proj1/sdk/v1/events", handle)
    # auto_decompress off: the handler reads the raw body to prove the SDK gzipped it.
    runner = web.AppRunner(app, auto_decompress=False)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    ep.url = f"http://127.0.0.1:{port}/ingest/org1/proj1"
    yield ep
    await runner.cleanup()


@pytest.fixture
def closed_url() -> Iterator[str]:
    """A URL on a port nothing listens on: connection refused."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    yield f"http://127.0.0.1:{port}/ingest/org1/proj1"
