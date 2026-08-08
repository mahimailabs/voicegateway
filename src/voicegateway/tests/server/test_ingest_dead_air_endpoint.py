"""``POST /v1/ingest/dead-air``: the collector-mode half of dead-air capture.

Same shape and same reasoning as ``/v1/ingest/turns``. Its own route because
``/v1/ingest`` builds a RequestRecord out of every dict it receives and counts
anything that fails to build as malformed, so an event posted there is answered
``200`` and dropped.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from voicegateway.core.gateway import Gateway
from voicegateway.server import build_app

_CFG: dict[str, Any] = {
    "providers": {"openai": {"api_key": "k"}},
    "models": {"stt": {}, "llm": {}, "tts": {}},
    "projects": {},
    "fallbacks": {"stt": [], "llm": [], "tts": []},
    "cost_tracking": {"enabled": True},
}

_KEY = "dead-air-test-key"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "dead_air.db"))
    monkeypatch.setenv("VOICEGW_API_KEY", _KEY)
    path = tmp_path / "voicegw.yaml"
    path.write_text(yaml.dump(_CFG))
    app = build_app(
        Gateway(config_path=str(path)), enable_mcp_sse=False, enable_dashboard=False
    )
    with TestClient(app) as c:
        yield c


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_KEY}"}


def _event(session_id: str, *, duration_ms: int = 4200) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "started_at_ms": 10_000,
        "duration_ms": duration_ms,
        "threshold_used_ms": 3000,
    }


def test_a_posted_event_is_readable_back(client) -> None:
    """The round trip. The reader is what was always empty."""
    posted = client.post(
        "/v1/ingest/dead-air", json=[_event("s-1")], headers=_auth()
    )
    assert posted.status_code == 200, posted.text
    assert posted.json() == {"accepted": 1}

    read = client.get("/api/sessions/s-1/dead_air", headers=_auth())
    assert read.status_code == 200, read.text
    body = read.json()
    events = body["events"] if isinstance(body, dict) else body
    assert len(events) == 1
    assert events[0]["duration_ms"] == 4200


def test_a_malformed_event_does_not_reject_the_batch(client) -> None:
    good = _event("s-2")
    bad = {"session_id": "s-2"}  # no timings: DeadAirEvent cannot be built
    resp = client.post("/v1/ingest/dead-air", json=[good, bad], headers=_auth())
    assert resp.status_code == 200
    assert resp.json() == {"accepted": 1}


def test_unknown_keys_are_ignored(client) -> None:
    """A newer agent must not 422 against an older collector."""
    row = _event("s-3")
    row["some_future_field"] = "whatever"
    resp = client.post("/v1/ingest/dead-air", json=[row], headers=_auth())
    assert resp.status_code == 200
    assert resp.json() == {"accepted": 1}


def test_it_requires_auth(client) -> None:
    resp = client.post("/v1/ingest/dead-air", json=[_event("s-4")])
    assert resp.status_code == 401


def test_the_batch_cap_is_enforced(client) -> None:
    rows = [_event(f"s-{i}") for i in range(2000)]
    resp = client.post("/v1/ingest/dead-air", json=rows, headers=_auth())
    assert resp.status_code == 413


def test_events_posted_to_the_request_route_are_not_silently_accepted(client) -> None:
    """Why this is a separate route rather than a discriminated record."""
    resp = client.post("/v1/ingest", json=[_event("s-5")], headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["accepted"] == 0, (
        "a dead-air event was accepted as a request record; piggybacking on "
        "/v1/ingest would be a silent drop"
    )
