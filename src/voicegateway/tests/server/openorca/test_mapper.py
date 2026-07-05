"""build_snapshot: roster rows collapse into logical-agent nodes + fleet rollup.

Two workers reporting the same ``agent_name`` collapse into one node whose
sessions sum and whose status is derived from the group; an agent whose only
worker is already ``offline`` (stale heartbeat) renders offline and is counted
in the fleet rollup.
"""

from __future__ import annotations

from typing import Any

from voicegateway.repository.workers_repository import RosterRow
from voicegateway.server.api.openorca.mapper import build_snapshot


def _row(**overrides: Any) -> RosterRow:
    base: dict[str, Any] = {
        "agent_id": "w1",
        "agent_name": "alpha",
        "project": "default",
        "tenant_id": None,
        "region": None,
        "version": None,
        "host": "host-1",
        "active_sessions": 1,
        "status": "busy",
        "started_at": None,
        "last_seen": 1000.0,
    }
    base.update(overrides)
    return RosterRow(**base)


def test_two_workers_same_agent_collapse_active_and_sum_sessions() -> None:
    rows = [
        _row(agent_id="w1", host="h1", active_sessions=2, status="busy"),
        _row(agent_id="w2", host="h2", active_sessions=3, status="idle"),
    ]
    snap = build_snapshot(rows, generated_at="2026-07-04T00:00:00+00:00")

    assert len(snap["agents"]) == 1
    agent = snap["agents"][0]
    assert agent["id"] == "alpha"
    assert agent["name"] == "alpha"
    # any worker busy -> node is active
    assert agent["status"] == "active"
    # sessions sum across the two workers
    assert agent["activeSessions"] == 5
    assert agent["currentAction"] == "5 live call(s)"
    assert agent["activityLevel"] == 100  # min(100, 5*20)
    assert agent["machineName"] == "2 workers"
    assert agent["graphAccess"] == "read"

    fleet = snap["fleetHealth"]
    assert fleet["totalAgents"] == 1
    assert fleet["activeAgents"] == 1
    assert fleet["offlineAgents"] == 0
    assert fleet["tasksInProgress"] == 5

    assert snap["meta"]["runtime"] == "voicegateway"
    assert snap["meta"]["generatedAt"] == "2026-07-04T00:00:00+00:00"
    assert snap["meta"]["connectionStatus"] == "connected"
    # the envelope collections the frontend still expects
    for key in ("tasks", "actionLog", "interventions", "swarms", "machines"):
        assert snap[key] == []


def test_all_offline_agent_is_offline_and_counted() -> None:
    rows = [_row(agent_name="beta", status="offline", active_sessions=0)]
    snap = build_snapshot(rows, generated_at="x")

    agent = snap["agents"][0]
    assert agent["status"] == "offline"
    assert snap["fleetHealth"]["offlineAgents"] == 1
    assert snap["fleetHealth"]["activeAgents"] == 0
    assert snap["fleetHealth"]["totalAgents"] == 1


def test_idle_when_only_idle_workers() -> None:
    rows = [_row(agent_name="gamma", status="idle")]
    snap = build_snapshot(rows, generated_at="x")

    agent = snap["agents"][0]
    assert agent["status"] == "idle"
    assert snap["fleetHealth"]["activeAgents"] == 0
    assert snap["fleetHealth"]["offlineAgents"] == 0


def test_fleet_counts_across_mixed_agents() -> None:
    rows = [
        _row(agent_name="alpha", status="busy", active_sessions=1),
        _row(agent_name="beta", status="idle", active_sessions=0),
        _row(agent_name="gamma", status="offline", active_sessions=0),
    ]
    snap = build_snapshot(rows, generated_at="x")

    fleet = snap["fleetHealth"]
    assert fleet["totalAgents"] == 3
    assert fleet["activeAgents"] == 1
    assert fleet["offlineAgents"] == 1
    assert fleet["tasksInProgress"] == 1
