"""Rendering for the livekit diagnostics. Plain strings so tests assert on
content; the CLI prints them through BaseCli/Rich.

The verdict is NOT decided here any more. :func:`check_json` used to carry its
own pass/warn/fail rules, which disagreed with the dashboard's
(``service._verdict``) on four inputs. Both now normalise into the one shape
:func:`voicegateway.livekit_diag.gates.evaluate_checks` reads, so the CLI and the
dashboard cannot drift again. See ``gates`` for the disagreement table and which
reading won.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from voicegateway.livekit_diag import gates
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
            # Show only components that were actually captured; a missing one must
            # not render as 0.00 (that reads as "instant"). Labels in pipeline order.
            labels = [
                ("eou", "turn-detect"),
                ("stt", "STT"),
                ("stt_ttfp", "STT-ttfp"),
                ("llm_ttft", "LLM-ttft"),
                ("tts", "TTS"),
            ]
            parts = [
                f"{label} {c[key]:.2f}"
                for key, label in labels
                if c.get(key) is not None
            ]
            lines.append("  " + " . ".join(parts))
        else:
            lines.append(
                "  breakdown (turn-detect/STT/LLM/TTS) needs an instrumented agent "
                "(voicegateway.attach) writing to the same local store, co-located"
            )
    return "\n".join(lines)


def _baseline_json(base) -> dict[str, Any] | None:
    if not base:
        return None
    return {
        "clients": base.clients,
        "rtt_ms": base.rtt_ms,
        "loss_pct": base.loss_pct,
        "quality": base.quality,
    }


def _check_results(agents, latency_results, base, steps, summarize) -> dict[str, Any]:
    """The CLI's raw probe objects, in ``execute_run``'s per-check shape.

    Written so the CLI and the dashboard feed the SAME gate function rather than
    two lookalike rule sets. ``ok`` is True throughout because the CLI's
    ``check`` aborts on any exception before it gets here; a per-agent probe
    failure is not an exception, it arrives as a result with zero trials.

    ``samples`` carries the raw per-trial seconds so the gate can compute a real
    p95 if there are ever enough of them. It does not appear in the returned
    payload: ``check --json`` is a documented output and this is gate input.
    """
    sfu_check = "sfu_load" if steps else "sfu"
    return {
        "agents": {"ok": True, "result": {"agents": agents_json(agents)}},
        "latency": {
            "ok": True,
            "result": {
                "agents": [
                    {
                        "agent": r.agent,
                        "stats": summarize(r),
                        "samples": list(r.e2e_samples),
                        "error": r.error,
                    }
                    for r in latency_results
                ]
            },
        },
        sfu_check: {
            "ok": True,
            "result": {
                "baseline": _baseline_json(base),
                "ramp": [
                    {
                        "clients": s.clients,
                        "rtt_ms": s.rtt_ms,
                        "loss_pct": s.loss_pct,
                        "quality": s.quality,
                    }
                    for s in (steps or [])
                ],
                # Both None until the CLI threads its --target-rtt-ms and a
                # normalised resource report through (``check`` runs the baseline
                # only, so it never produces a ramp). Without the threshold the
                # ramp was measured against, the capacity gate reports that it
                # could not evaluate rather than inventing one.
                "target_rtt_ms": None,
                "resource": None,
            },
        },
    }


def check_json(
    agents,
    latency_results,
    base,
    steps,
    resource,
    knee,
    summarize,
    target_ms: float = 1500.0,
    *,
    strict: bool = False,
) -> dict:
    """The ``check --json`` payload, with the verdict decided by ``gates``.

    ``gates`` is a new, additive key: every field that was here before is
    unchanged and in the same place. ``verdict`` can now also be ``UNKNOWN``,
    which is what a run that could not evaluate a gate reports instead of the
    PASS it used to.
    """
    gate_results = gates.evaluate_checks(
        _check_results(agents, latency_results, base, steps, summarize),
        target_ms,
        strict=strict,
    )
    return {
        "agents": agents_json(agents),
        "latency": [
            {"agent": r.agent, "stats": summarize(r), "components": r.components}
            for r in latency_results
        ],
        "sfu": {
            "baseline": _baseline_json(base),
            "ramp": [
                {"clients": s.clients, "rtt_ms": s.rtt_ms, "loss_pct": s.loss_pct}
                for s in (steps or [])
            ],
            "knee": knee,
        },
        "gates": [g.as_dict() for g in gate_results],
        "verdict": gates.verdict(gate_results),
    }


def render_check(
    agents,
    latency_results,
    base,
    steps,
    resource,
    knee,
    summarize,
    target_ms,
    *,
    strict: bool = False,
) -> str:
    js = check_json(
        agents,
        latency_results,
        base,
        steps,
        resource,
        knee,
        summarize,
        target_ms,
        strict=strict,
    )
    parts = [
        f"VERDICT: {js['verdict']}",
        # Every gate, not just the failing ones: a CI log that says only
        # "VERDICT: UNKNOWN" sends the reader back to the machine that produced
        # it. The gate that decided the verdict is named on its own line, with
        # the statistic that decided it spelled out.
        *[f"  [{g['status']}] {g['gate']}: {g['detail']}" for g in js["gates"]],
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
            f"{t.get('clients', 0)}({t.get('vantages', 0)}v)-> {t.get('rtt_ms', 0)}ms "
            f"{t.get('loss_pct', 0)}% {t.get('quality', 'Unknown')}"
            for t in combined
        )
        knee_txt = f"combined knee ~{knee} clients" if knee else "no knee within ramp"
        lines.append(f"  combined: {seg}   {knee_txt}")
    for v in vantages:
        vseg = " . ".join(
            f"{s.get('clients', 0)}-> {s.get('rtt_ms', 0)}ms {s.get('loss_pct', 0)}%"
            for s in v.get("steps", [])
        )
        lines.append(f"  {str(v.get('vantage', '')):12.12}: {vseg}")
    dropped = result.get("dropped") or []
    if dropped:
        lines.append(f"  dropped (no steps reported): {', '.join(dropped)}")
    return "\n".join(lines)
