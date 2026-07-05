"""Map the fleet roster onto the OpenOrca dashboard snapshot shape.

The OpenOrca UI consumes a ``ClawOrchestratorData`` snapshot: a set of logical
agents (nodes), plus a fleet-health rollup and a small envelope of empty
collections the frontend still expects (tasks, action log, interventions,
swarms, machines). Each node here is a logical agent keyed by ``agent_name``:
every worker that reports the same ``agent_name`` collapses into one node whose
status and session count aggregate its workers. A worker whose heartbeat has
aged past the roster TTL already arrives with ``status == "offline"`` (see
:func:`voicegateway.repository.workers_repository.read_roster`), so an agent
whose workers are all stale renders offline without any special-casing here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from voicegateway.repository.workers_repository import RosterRow


def _agent_status(statuses: list[str]) -> str:
    """Collapse a group's worker statuses into one node status.

    ``active`` when any worker is ``busy``; else ``idle`` when any worker is
    ``busy`` or ``idle``; else ``offline`` (every worker stale/offline).
    """
    if any(s == "busy" for s in statuses):
        return "active"
    if any(s in ("busy", "idle") for s in statuses):
        return "idle"
    return "offline"


def _build_agent(agent_name: str, group: list[RosterRow]) -> dict[str, Any]:
    """Build one FULL ClawAgent dict from a group of same-named workers."""
    n = len(group)
    active_sessions = sum(row.active_sessions for row in group)
    status = _agent_status([row.status for row in group])
    machine_name = f"{n} worker" if n == 1 else f"{n} workers"
    return {
        "id": agent_name,
        "name": agent_name,
        "machineId": group[0].host or agent_name,
        "machineName": machine_name,
        "status": status,
        "domain": group[0].project,
        "integrations": [],
        "currentTaskId": None,
        "currentAction": f"{active_sessions} live call(s)",
        "memoryUsage": 0,
        "uptime": "",
        "tasksCompleted": 0,
        "collaboratingWith": [],
        "interventionRequired": False,
        "activityLevel": min(100, active_sessions * 20),
        "loadedCores": [],
        "knowledgeContributions": 0,
        "graphAccess": "read",
        "activeSessions": active_sessions,
    }


def build_snapshot(rosters: list[RosterRow], *, generated_at: str) -> dict[str, Any]:
    """Build the OpenOrca snapshot dict from the current fleet roster."""
    groups: dict[str, list[RosterRow]] = {}
    for row in rosters:
        groups.setdefault(row.agent_name, []).append(row)

    agents = [_build_agent(name, group) for name, group in groups.items()]

    active_agents = sum(1 for a in agents if a["status"] == "active")
    offline_agents = sum(1 for a in agents if a["status"] == "offline")
    tasks_in_progress = sum(a["activeSessions"] for a in agents)

    fleet_health = {
        "totalAgents": len(agents),
        "activeAgents": active_agents,
        "offlineAgents": offline_agents,
        "interventionsRequired": 0,
        "tasksInProgress": tasks_in_progress,
        "tasksCompletedToday": 0,
        "swarmsActive": 0,
        "overallHealth": "healthy",
    }

    return {
        "agents": agents,
        "tasks": [],
        "actionLog": [],
        "interventions": [],
        "swarms": [],
        "machines": [],
        "fleetHealth": fleet_health,
        "meta": {
            "runtime": "voicegateway",
            "generatedAt": generated_at,
            "connectionStatus": "connected",
        },
    }


__all__ = ["build_snapshot"]
