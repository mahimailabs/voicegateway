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


def render_latency(results: list, target_ms: float, summarize) -> str:
    lines = []
    for r in results:
        s = summarize(r)
        if not s["trials"]:
            lines.append(f"{r.agent:14} no successful probe ({r.error or 'no reply'})")
            continue
        verdict = "GOOD" if s["avg"] * 1000 <= target_ms else "SLOW"
        head = (
            f"{r.agent:14} E2E avg {s['avg']:.2f}s  p50 {s['p50']:.2f}s  "
            f"p95 {s['p95']:.2f}s   {verdict} (<{target_ms/1000:.1f}s)"
        )
        lines.append(head)
        net = f"network(probe->SFU) {r.network_s:.2f}" if r.network_s is not None else "network n/a"
        if r.components:
            c = r.components
            lines.append(
                f"  {net} . turn-detect {c.get('eou', 0):.2f} . STT {c.get('stt', 0):.2f} "
                f". LLM-ttft {c.get('llm_ttft', 0):.2f} . TTS {c.get('tts', 0):.2f}"
            )
        else:
            lines.append(f"  {net} . breakdown (turn-detect/STT/LLM/TTS) lands in Phase 2 (collector correlation)")
    return "\n".join(lines)
