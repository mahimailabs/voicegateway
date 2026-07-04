"""Rendering + verdict helpers for the livekit diagnostics. Plain strings so
tests assert on content; the CLI prints them through BaseCli/Rich.
"""

from __future__ import annotations

from dataclasses import asdict

from voicegateway.livekit_diag.admin import AgentRow

_ROSTER_NOTE = (
    "Idle/registered workers are not reported by LiveKit's server API; "
    "run the Phase 2 heartbeat to see the full roster."
)


def render_agents(rows: list[AgentRow]) -> str:
    lines = [f"{'AGENT':16} {'ROOM':22} {'STATE':11} {'IN-CALL':8} {'AGE':6}"]
    for r in rows:
        age = f"{int(r.age_s)}s" if r.age_s is not None else "-"
        lines.append(
            f"{r.agent_name:16.16} {r.room:22.22} {r.state:11} "
            f"{r.humans:<8} {age:6}"
        )
    room_count = len({r.room for r in rows})
    lines.append("")
    lines.append(f"{len(rows)} agents active in {room_count} rooms. {_ROSTER_NOTE}")
    return "\n".join(lines)


def agents_json(rows: list[AgentRow]) -> list[dict]:
    sorted_rows = sorted(rows, key=lambda r: r.agent_name)
    return [asdict(r) for r in sorted_rows]
