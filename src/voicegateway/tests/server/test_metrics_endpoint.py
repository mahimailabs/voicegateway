"""Tests for the v0.2.0 metrics endpoints (T12).

These endpoints live under
:mod:`voicegateway.server.api.dashboard` (sessions.py + metrics.py)
and are reached through the daemon's ``build_app(...)``.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.middleware.dead_air_detector_middleware import DeadAirEvent
from voicegateway.middleware.turn_tracker_middleware import TurnRow
from voicegateway.repository import (
    dead_air_repository as dead_air,
)
from voicegateway.repository import (
    turns_repository as turns,
)
from voicegateway.server.main import build_app


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "metrics-endpoint.db"))
    return Gateway(config_path=temp_config)


@pytest.fixture
async def client(gateway):
    app = build_app(gateway, enable_mcp_sse=False, enable_dashboard=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_turns(gateway, session_id: str, count: int = 3) -> None:
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        rows = []
        for i in range(count):
            rows.append(
                TurnRow(
                    session_id=session_id,
                    turn_index=i,
                    caller_speak_start_ms=i * 1000,
                    caller_speak_end_ms=i * 1000 + 500,
                    agent_speak_start_ms=i * 1000 + 600,
                    agent_speak_end_ms=i * 1000 + 900,
                    response_speed_ms=100 + i,
                )
            )
        await turns.create_turns_bulk(db, rows)


async def _seed_dead_air(gateway, session_id: str, count: int = 2) -> None:
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        for i in range(count):
            await dead_air.create_event(
                db,
                DeadAirEvent(
                    session_id=session_id,
                    started_at_ms=i * 1000,
                    duration_ms=3500,
                    threshold_used_ms=3000,
                ),
            )


async def test_session_turns_endpoint_returns_ordered_turns(client, gateway) -> None:
    await _seed_turns(gateway, "s1", count=3)
    resp = await client.get("/api/sessions/s1/turns")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "s1"
    assert len(body["turns"]) == 3
    assert [t["turn_index"] for t in body["turns"]] == [0, 1, 2]
    assert body["turns"][0]["response_speed_ms"] == 100


async def test_session_turns_endpoint_unknown_session(client) -> None:
    resp = await client.get("/api/sessions/ghost/turns")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"session_id": "ghost", "turns": []}


async def test_session_dead_air_endpoint_returns_events(client, gateway) -> None:
    await _seed_dead_air(gateway, "s1", count=2)
    resp = await client.get("/api/sessions/s1/dead_air")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "s1"
    assert len(body["events"]) == 2
    assert body["events"][0]["duration_ms"] == 3500
    assert body["events"][0]["threshold_used_ms"] == 3000


async def test_metrics_summary_window_shape(client) -> None:
    """The /api/metrics shape covers all four cards plus the window meta."""
    resp = await client.get("/api/metrics?days=7")
    assert resp.status_code == 200
    body = resp.json()
    assert body["window"]["days"] == 7
    assert "since" in body["window"] and "until" in body["window"]
    assert body["filter"]["project"] is None
    assert "session_count" in body
    assert "measured_session_count" in body
    assert "per_minute_cost_usd_avg" in body
    assert body["response_speed_ms"].keys() == {"p50", "p95"}
    assert "talk_over_rate" in body
    assert "dead_air_event_count" in body


async def test_metrics_summary_empty_window_returns_nulls(client) -> None:
    """Empty window: counts are 0, averages are null."""
    resp = await client.get("/api/metrics?days=1")
    body = resp.json()
    assert body["session_count"] == 0
    assert body["measured_session_count"] == 0
    assert body["per_minute_cost_usd_avg"] is None
    assert body["response_speed_ms"]["p50"] is None
    assert body["response_speed_ms"]["p95"] is None
    assert body["talk_over_rate"] is None
    assert body["dead_air_event_count"] == 0


async def test_metrics_summary_rejects_out_of_range_days(client) -> None:
    """Days clamped to [1, 365] by the query annotation."""
    resp = await client.get("/api/metrics?days=0")
    assert resp.status_code == 422
    resp = await client.get("/api/metrics?days=1000")
    assert resp.status_code == 422
