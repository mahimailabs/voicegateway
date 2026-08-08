"""``POST /v1/ingest/turns``: the collector-mode half of the turn write path.

Binding a tracker alone only fixes the co-located case, where the agent and the
collector share a database. That is the exact limitation
``/v1/rooms/{room}/latency`` exists to escape, so a fleet agent needs somewhere
to push turns to. This is that route.

It is deliberately NOT a discriminated record on ``/v1/ingest``: that handler
builds a RequestRecord out of every dict it receives and counts anything that
fails to build as malformed, so a turn posted there is answered ``200`` and
dropped. The last test here pins that reasoning.
"""

from __future__ import annotations

import time
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

_KEY = "turns-test-key"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "turns.db"))
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


def _turn(session_id: str, index: int, *, start: int = 1000) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "turn_index": index,
        "caller_speak_start_ms": start,
        "caller_speak_end_ms": start + 500,
        "agent_speak_start_ms": start + 900,
        "agent_speak_end_ms": start + 2000,
        "response_speed_ms": 400,
    }


def test_a_posted_turn_is_readable_back(client) -> None:
    """The round trip. Anything less proves only that the route returns 200."""
    posted = client.post(
        "/v1/ingest/turns", json=[_turn("s-1", 0)], headers=_auth()
    )
    assert posted.status_code == 200, posted.text
    assert posted.json() == {"accepted": 1}

    read = client.get("/api/sessions/s-1/turns", headers=_auth())
    assert read.status_code == 200, read.text
    rows = read.json()
    body = rows["turns"] if isinstance(rows, dict) else rows
    assert len(body) == 1
    assert body[0]["turn_index"] == 0
    assert body[0]["response_speed_ms"] == 400


def test_it_reaches_the_room_latency_endpoint(client) -> None:
    """The reason the route exists: e2e_ms was always null in collector mode.

    Requests carry the room, turns carry the session id, and rooms.py bridges
    room -> session_id -> turns. Both halves must be posted for e2e_ms to be
    anything other than null, so both are posted here.
    """
    now = int(time.time())
    record = {
        "id": "req-1",
        "timestamp": now,
        "project": "p",
        "modality": "llm",
        "model_id": "openai/gpt-4o-mini",
        "provider": "openai",
        "input_units": 100,
        "output_units": 50,
        "pricing_source": "test",
        "ttfb_ms": 447.0,
        "status": "success",
        "session_id": "s-room",
        "metadata": {"room": "room-1"},
    }
    assert client.post("/v1/ingest", json=[record], headers=_auth()).status_code == 200
    assert (
        client.post(
            "/v1/ingest/turns", json=[_turn("s-room", 0)], headers=_auth()
        ).status_code
        == 200
    )

    got = client.get("/v1/rooms/room-1/latency", headers=_auth())
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["turn_count"] == 1, body
    assert body["e2e_ms"] is not None, "e2e_ms is still null with a turn stored"
    assert len(body["turns"]) == 1


def test_a_malformed_turn_does_not_reject_the_batch(client) -> None:
    """One bad row must not cost the good ones, matching /v1/ingest."""
    good = _turn("s-2", 0)
    bad = {"session_id": "s-2"}  # no boundaries: TurnRow cannot be built
    resp = client.post("/v1/ingest/turns", json=[good, bad], headers=_auth())
    assert resp.status_code == 200
    assert resp.json() == {"accepted": 1}


def test_unknown_keys_are_ignored(client) -> None:
    """A newer agent posting an extra field must not 422 against an older
    collector, same tolerance ``_record_from_payload`` gives requests."""
    row = _turn("s-3", 0)
    row["some_future_field"] = "whatever"
    resp = client.post("/v1/ingest/turns", json=[row], headers=_auth())
    assert resp.status_code == 200
    assert resp.json() == {"accepted": 1}


def test_it_requires_auth(client) -> None:
    """Public by necessity, so it must refuse an unauthenticated write."""
    resp = client.post("/v1/ingest/turns", json=[_turn("s-4", 0)])
    assert resp.status_code == 401


def test_the_batch_cap_is_enforced(client) -> None:
    """Same 413 guard as /v1/ingest; an unbounded batch is a memory problem."""
    rows = [_turn("s-5", i) for i in range(2000)]
    resp = client.post("/v1/ingest/turns", json=rows, headers=_auth())
    assert resp.status_code == 413


def test_turns_posted_to_the_request_route_are_not_silently_accepted(client) -> None:
    """Why this is a separate route rather than a discriminated record.

    ``/v1/ingest`` answers 200 and reports how many records it accepted. A turn
    row sent there builds no RequestRecord, so it is counted as malformed and
    dropped, and the agent sees success. That silent drop is the whole class of
    bug this endpoint exists to end, so it is pinned rather than assumed.
    """
    resp = client.post("/v1/ingest", json=[_turn("s-6", 0)], headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["accepted"] == 0, (
        "a turn row was accepted as a request record; piggybacking turns on "
        "/v1/ingest would be a silent drop"
    )
