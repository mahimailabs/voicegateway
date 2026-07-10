"""Map the fleet roster onto the OpenOrca dashboard snapshot shape.

The OpenOrca UI consumes a ``ClawOrchestratorData`` snapshot: a set of logical
agents (nodes), plus a fleet-health rollup and a small envelope of empty
collections the frontend still expects (tasks, action log, swarms, machines).
Each node here is a logical agent keyed by ``agent_name``:
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
    ``idle``; else ``offline`` (every worker stale/offline).
    """
    if any(s == "busy" for s in statuses):
        return "active"
    if any(s == "idle" for s in statuses):
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


def _field(obj: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a dict or an object attribute."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


_DOMAIN_BY_PROJECT_HINT = {
    "realty": "communications",
    "portfolio": "research",
    "appointment": "productivity",
    "demo": "communications",
}


def _domain_for_project(project: str | None) -> str:
    """Map a project to an OpenOrca AgentDomain. Voice calls default to
    ``communications``; a few name hints refine it."""
    if not project:
        return "communications"
    p = project.lower()
    for hint, domain in _DOMAIN_BY_PROJECT_HINT.items():
        if hint in p:
            return domain
    return "communications"


def _build_tasks(sessions: list[Any]) -> list[dict[str, Any]]:
    """One OpenOrca AgentTask per recent/live session.

    Duck-typed: a session is any object (a storage row or a plain object) with
    ``session_id`` (or ``id``) and ``started_at``; ``agent_id``/``project``/
    ``ended_at``/``error``/``summary`` are optional. A live session (no
    ``ended_at``) is ``in_progress`` at 0%; an ended one is ``completed`` (or
    ``failed`` if it saw an error) at 100%.
    """
    tasks: list[dict[str, Any]] = []
    for s in sessions:
        session_id = _field(s, "session_id", None) or _field(s, "id", None)
        if not session_id:
            continue
        agent = _field(s, "agent_id", None) or _field(s, "project", None) or "agent"
        ended = _field(s, "ended_at", None)
        errored = bool(_field(s, "error", False))
        status = (
            "in_progress" if ended is None else ("failed" if errored else "completed")
        )
        tasks.append(
            {
                "id": str(session_id),
                "agentId": str(agent),
                "agentName": str(agent),
                "domain": _domain_for_project(_field(s, "project", None)),
                "title": "Voice call",
                "description": _field(s, "summary", "") or "",
                "status": status,
                "priority": "medium",
                "progress": 0 if ended is None else 100,
                "startTime": _field(s, "started_at", "") or "",
                "integrationsUsed": [],
                "collaborators": [],
            }
        )
    return tasks


def build_snapshot(
    rosters: list[RosterRow],
    *,
    sessions: list[Any] | None = None,
    generated_at: str,
) -> dict[str, Any]:
    """Build the OpenOrca snapshot from the roster, plus recent sessions (mapped
    to tasks)."""
    groups: dict[str, list[RosterRow]] = {}
    for row in rosters:
        groups.setdefault(row.agent_name, []).append(row)

    agents = [_build_agent(name, group) for name, group in groups.items()]
    tasks = _build_tasks(sessions or [])

    active_agents = sum(1 for a in agents if a["status"] == "active")
    offline_agents = sum(1 for a in agents if a["status"] == "offline")

    fleet_health = {
        "totalAgents": len(agents),
        "activeAgents": active_agents,
        "offlineAgents": offline_agents,
        "interventionsRequired": 0,
        "tasksInProgress": sum(a["activeSessions"] for a in agents),
        "tasksCompletedToday": sum(1 for t in tasks if t["status"] == "completed"),
        "swarmsActive": 0,
        "overallHealth": "healthy",
    }

    return {
        "agents": agents,
        "tasks": tasks,
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
