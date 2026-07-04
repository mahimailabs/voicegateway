"""Rendering + verdict helpers for the livekit diagnostics. Plain strings so
tests assert on content; the CLI prints them through BaseCli/Rich.
"""

from __future__ import annotations

from dataclasses import asdict

from voicegateway.livekit_diag.admin import AgentRow

_ROSTER_NOTE = (
    "Idle/registered workers are not reported by LiveKit's server API. Set "
    "VOICEGW_COLLECTOR_URL + VOICEGW_API_KEY (and run register_worker in your "
    "agents) to also list the heartbeat roster."
)


def render_agents(rows: list[AgentRow], roster: list[dict] | None = None) -> str:
    """Render the in-room agents, plus the heartbeat roster when it is available.

    ``roster`` is None when the collector is not configured (we then print the
    note that says how to enable it); an empty list means it is configured but no
    workers have reported yet.
    """
    lines = [f"{'AGENT':16} {'ROOM':22} {'STATE':11} {'IN-CALL':8} {'AGE':6}"]
    for r in rows:
        age = f"{int(r.age_s)}s" if r.age_s is not None else "-"
        lines.append(
            f"{r.agent_name:16.16} {r.room:22.22} {r.state:11} {r.humans:<8} {age:6}"
        )
    room_count = len({r.room for r in rows})
    lines.append("")
    lines.append(f"{len(rows)} agents active in {room_count} rooms.")
    if roster is None:
        lines.append(_ROSTER_NOTE)
        return "\n".join(lines)
    lines.append("")
    lines.append("Registered workers (heartbeat roster):")
    lines.append(f"{'AGENT':16} {'STATUS':9} {'REGION':10} {'VERSION':10}")
    for w in roster:
        lines.append(
            f"{str(w.get('agent_name') or ''):16.16} "
            f"{str(w.get('status') or ''):9} "
            f"{str(w.get('region') or '-'):10.10} "
            f"{str(w.get('version') or ''):10.10}"
        )
    idle = sum(1 for w in roster if w.get("status") == "idle")
    busy = sum(1 for w in roster if w.get("status") == "busy")
    offline = sum(1 for w in roster if w.get("status") == "offline")
    lines.append(
        f"{len(roster)} workers registered ({idle} idle, {busy} busy, {offline} offline)."
    )
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
            f"p95 {s['p95']:.2f}s   {verdict} (<{target_ms / 1000:.1f}s)"
        )
        lines.append(head)
        if r.components:
            c = r.components
            lines.append(
                f"  turn-detect {c.get('eou', 0):.2f} . STT {c.get('stt', 0):.2f} "
                f". LLM-ttft {c.get('llm_ttft', 0):.2f} . TTS {c.get('tts', 0):.2f}"
            )
        else:
            lines.append(
                "  breakdown (turn-detect/STT/LLM/TTS) needs an instrumented agent "
                "(voicegateway.attach) writing to the same local store, co-located"
            )
    return "\n".join(lines)


def check_json(
    agents,
    latency_results,
    base,
    steps,
    resource,
    knee,
    summarize,
    target_ms: float = 1500.0,
) -> dict:
    verdict = "PASS"
    for r in latency_results:
        s = summarize(r)
        if not s["trials"]:
            verdict = "WARN"
        elif s["avg"] * 1000 > target_ms:
            verdict = "WARN"
    if base and (base.loss_pct > 1.0 or base.quality in {"Poor", "Lost"}):
        verdict = "FAIL"
    return {
        "agents": agents_json(agents),
        "latency": [
            {"agent": r.agent, "stats": summarize(r), "components": r.components}
            for r in latency_results
        ],
        "sfu": {
            "baseline": {
                "clients": base.clients,
                "rtt_ms": base.rtt_ms,
                "loss_pct": base.loss_pct,
                "quality": base.quality,
            }
            if base
            else None,
            "ramp": [
                {"clients": s.clients, "rtt_ms": s.rtt_ms, "loss_pct": s.loss_pct}
                for s in (steps or [])
            ],
            "knee": knee,
        },
        "verdict": verdict,
    }


def render_check(
    agents, latency_results, base, steps, resource, knee, summarize, target_ms
) -> str:
    js = check_json(
        agents, latency_results, base, steps, resource, knee, summarize, target_ms
    )
    parts = [
        f"VERDICT: {js['verdict']}",
        "",
        render_agents(agents),
        "",
        render_latency(latency_results, target_ms, summarize),
        "",
        render_sfu("co-located", base, steps, resource, knee),
    ]
    return "\n".join(parts)


def render_sfu(vantage: str, baseline, ramp_steps, resource, knee) -> str:
    lines = [
        f"SFU  vantage: {vantage}   baseline: rtt {baseline.rtt_ms}ms . "
        f"loss {baseline.loss_pct}% . {baseline.quality}"
    ]
    if ramp_steps:
        seg = " . ".join(
            f"{s.clients}-> {s.rtt_ms}ms {s.loss_pct}%" for s in ramp_steps
        )
        knee_txt = f"knee ~{knee} clients" if knee else "no knee within ramp"
        lines.append(f"  ramp: {seg}   {knee_txt}")
    if resource:
        sat = (
            " (prober saturated: results reflect this host, not the SFU)"
            if resource.saturated
            else ""
        )
        lines.append(
            f"  prober: ~{resource.per_client['cpu_pct']}% CPU + "
            f"~{resource.per_client['kbps_up']} kbps up per client; "
            f"host sustains ~{resource.sustainable_n} before CPU-bound{sat}"
        )
    return "\n".join(lines)


def render_distributed_sfu(result: dict) -> str:
    """Render a multi-vantage distributed SFU run (coordinator aggregate).

    Combined tiers show the SFU's total concurrent load and the worst rtt / loss
    / quality any vantage saw; the per-vantage lines below attribute it.
    """
    combined = result.get("combined") or []
    vantages = result.get("vantages") or []
    knee = result.get("knee")
    lines = [f"SFU  distributed: {len(vantages)} vantages"]
    if combined:
        seg = " . ".join(
            f"{t['clients']}({t['vantages']}v)-> {t['rtt_ms']}ms "
            f"{t['loss_pct']}% {t['quality']}"
            for t in combined
        )
        knee_txt = f"combined knee ~{knee} clients" if knee else "no knee within ramp"
        lines.append(f"  combined: {seg}   {knee_txt}")
    for v in vantages:
        vseg = " . ".join(
            f"{s['clients']}-> {s['rtt_ms']}ms {s['loss_pct']}%" for s in v["steps"]
        )
        lines.append(f"  {v['vantage']:12.12}: {vseg}")
    dropped = result.get("dropped") or []
    if dropped:
        lines.append(f"  dropped (no steps reported): {', '.join(dropped)}")
    return "\n".join(lines)
