"""THE diagnostics run report: one payload, two renderings, no web framework.

This module is the whole report. It was extracted out of
``server/api/dashboard/diagnostics.py`` unchanged so that the export is reachable
without FastAPI: CI wants the artifact from ``voicegw livekit report`` on a box
that never runs the dashboard, and the dashboard endpoint wants the identical
bytes. The endpoint now calls :func:`build_payload`, :func:`render_html` and
:func:`report_filename` here rather than keeping a copy of them. Two copies of a
renderer are two reports that disagree the first time one of them is edited, and
the report is the artifact a client actually reads.

Nothing here imports fastapi, starlette or the server package, and nothing here
touches storage: the caller hands in a :class:`RunRecord` (already read from
wherever it lives) plus the LiveKit URL it resolved, and gets back a dict and a
string.

The report is a deliverable, not a debug dump: an operator attaches it to a
ticket or hands it to a client, and it is read months later by somebody who was
not in the room when it ran. That drives every decision below.

* **One file, no network.** The HTML carries its own CSS inline and references
  nothing external -- no CDN, no stylesheet, no font, no image, no script. It
  renders identically from ``file://`` on a laptop with no internet.
* **It carries its own context.** When the run happened, what it was pointed
  at, the thresholds it was measured against, the tool version, and the schema
  version are all in the document; a number alone is worthless out of band.
* **It cannot look more complete than the run was.** A check that was not
  requested, recorded nothing, or errored says exactly that where its numbers
  would have been. Nothing unmeasured renders as 0, and nothing renders as
  ``-`` where "measured as zero" and "never measured" differ.
* **It does not decide anything.** The verdict and the per-gate reasoning are
  read back out of what :mod:`voicegateway.livekit_diag.gates` recorded on the
  run. Re-judging an old run at export time would produce a report that
  disagrees with the CI log the same run printed.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from voicegateway._version import __version__
from voicegateway.livekit_diag import gates

#: Bumped only for a BREAKING change to the report payload.
#:
#: The contract for consumers, and the reason this field exists at all:
#:
#: * v1 is the first published shape.
#: * Within a major version the payload is ADDITIVE ONLY. New keys may appear;
#:   an existing key never changes meaning, type or nesting, and never
#:   disappears. A parser must therefore ignore keys it does not recognise.
#: * A value that was not measured is ``null`` (or the literal string
#:   ``"not_measured"`` for a field that is structurally always absent, e.g.
#:   packet loss). It is never 0, and a consumer must not coerce it to one.
#: * Anything that would break a v1 parser -- a removed key, a retyped value, a
#:   moved nesting level -- lands as ``schema_version: 2`` instead.
#:
#: Moving this code out of the dashboard module changed nothing about the
#: payload, so it is still 1: the CLI publishes the same v1 shape the endpoint
#: has been publishing, and a v1 parser reads both.
REPORT_SCHEMA_VERSION = 1

#: What ``schema_version`` versions. Lets a consumer that reads several
#: VoiceGateway exports tell them apart without guessing from shape.
REPORT_KIND = "voicegateway.diagnostics.run_report"

#: The machine token for a value nobody measured. The HTML spells it "not
#: measured". Both are the same claim, and neither is ever a zero.
NOT_MEASURED = "not_measured"

# Public tool version (the local-build "+g<sha>" suffix is noise in a
# deliverable, and it leaks a working-tree state to whoever receives the file).
_PUBLIC_VERSION = __version__.split("+", 1)[0]


@dataclass
class RunRecord:
    """One diagnostics run, as the report reads it.

    The dashboard mutates one of these through a run's life; the CLI builds one
    from a stored row (:func:`run_from_row`). Both hand the same object to
    :func:`build_payload`, which is the point: the report cannot see a different
    run depending on who asked for it.
    """

    run_id: str
    checks: list[str]
    config: dict[str, Any]
    status: str = "queued"
    results: dict[str, Any] | None = None
    verdict: str | None = None
    error: str | None = None
    created_at: str = field(default_factory=str)
    started_at: str | None = None
    ended_at: str | None = None


def run_from_row(row: dict[str, Any]) -> RunRecord:
    """Rehydrate a stored ``diagnostics_runs`` row into a :class:`RunRecord`.

    ``project`` is dropped on purpose: it exists so a run ages out on the
    per-project retention pass and has never been part of this payload.
    """
    return RunRecord(
        run_id=row["run_id"],
        checks=row["checks"],
        config=row["config"],
        status=row["status"],
        results=row["results"],
        verdict=row["verdict"],
        error=row["error"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
    )


def _generated_at() -> str:
    """When this export was produced. The only value in the payload that is not
    a property of the run, which is why it lives behind a function: freeze it and
    two exports of one run are byte-identical."""
    return datetime.now(UTC).isoformat()


def _utc(at_ms: Any) -> str | None:
    """Epoch milliseconds as an ISO-8601 UTC instant, or None.

    UTC and explicit about it. A capacity report is read months later by people
    in other places, and a bare local timestamp is unresolvable by then.
    """
    value = _as_int(at_ms)
    if value is None:
        return None
    return (
        datetime.fromtimestamp(value / 1000, UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


# Verdicts are read, never derived, so this maps only what gates can produce.
# UNKNOWN is spelled out at length because it is the whole reason the gate work
# happened: a run that could not evaluate used to report a clean PASS, and a
# report that renders UNKNOWN as a tick would put that bug back into the one
# artifact the client actually reads.
_VERDICT_MEANING = {
    gates.PASS: (
        "Every gate this run evaluated was inside its threshold. It says nothing "
        "about anything the run did not measure."
    ),
    gates.WAIVED: (
        "At least one gate was explicitly WAIVED: it was not enforced for this "
        "run, and somebody recorded why. This is NOT a pass. The requirement was "
        "not withdrawn, it was set aside, and the reason is printed on the gate "
        "row so the risk that was accepted is legible rather than absent."
    ),
    gates.WARN: (
        "Every gate evaluated, and at least one measured value is outside the "
        "threshold it was compared against."
    ),
    gates.UNKNOWN: (
        "At least one gate could NOT be evaluated. This is NOT a pass: the run "
        "did not demonstrate a healthy deployment, it failed to measure "
        "something. Read the gate rows below for which one, and treat the "
        "un-measured half as unknown rather than fine."
    ),
    gates.FAIL: ("At least one gate measured a failure, or a check did not complete."),
}

# What this report structurally cannot tell you. Carried in the payload (not just
# the HTML) so an automated consumer inherits the caveats with the numbers.
_REPORT_LIMITS = [
    "Packet loss is not measured. The client SDK does not expose per-connection "
    "loss to the probe, so no loss figure and no loss series appears anywhere in "
    "this report. Connection quality is the coarse signal that is real.",
    "SFU round-trip time is the prober's own message through the SFU data "
    "channel and back. It is not the latency a caller hears an agent answer "
    "with; that is the reply-latency section.",
    "Reply latency is the slowest of at most three billed trial calls per agent. "
    f"Below {gates.MIN_PERCENTILE_SAMPLES} samples a percentile is not a "
    'percentile, so the tail is reported as "max of N" and never as a p95.',
    "LiveKit's server API reports a worker only once it has joined a room, so an "
    "idle registered agent is invisible to the agents check unless a heartbeat "
    "roster travelled with the run.",
    "Every number here comes from one vantage point: the host that ran the "
    "probe. It measures the path between that host and the deployment, not the "
    "path any particular caller takes.",
]


def _as_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _ms(seconds: Any) -> float | None:
    """Seconds -> milliseconds, keeping None as None (never 0.0)."""
    value = _as_float(seconds)
    return None if value is None else value * 1000.0


def _check_state(run: RunRecord, checks: dict[str, Any], name: str) -> dict[str, Any]:
    """How one check went, in the four states a report has to tell apart.

    ``not_requested`` (nobody asked for it), ``no_result`` (asked for, nothing
    recorded -- the run is still going or died), ``errored`` (it ran and broke)
    and ``ok``. Collapsing any two of these is how a report ends up looking more
    complete than the run was.
    """
    entry = checks.get(name)
    if not isinstance(entry, dict):
        state = "not_requested" if name not in run.checks else "no_result"
        return {"state": state, "error": None, "result": None}
    if not entry.get("ok"):
        return {
            "state": "errored",
            "error": str(entry.get("error") or "no reason was recorded"),
            "result": None,
        }
    result = entry.get("result")
    return {
        "state": "ok",
        "error": None,
        "result": result if isinstance(result, dict) else {},
    }


def _agents_finding(run: RunRecord, checks: dict[str, Any]) -> dict[str, Any]:
    """The two agent populations, kept apart.

    ``roster_configured`` is False when no collector was configured (nobody was
    asked) and True with an empty list when one was asked and reported nothing.
    Collapsing them would print a missing collector as an empty fleet.
    """
    got = _check_state(run, checks, "agents")
    out: dict[str, Any] = {
        "state": got["state"],
        "error": got["error"],
        "in_room": [],
        "in_room_count": None,
        "rooms": None,
        "roster_configured": None,
        "roster": [],
        "roster_counts": None,
    }
    result = got["result"]
    if result is None:
        return out
    rows = [r for r in (result.get("agents") or []) if isinstance(r, dict)]
    out["in_room"] = [
        {
            "agent_name": r.get("agent_name") or None,
            "room": r.get("room") or None,
            "state": r.get("state") or None,
            "humans": _as_int(r.get("humans")),
            # None when the server reported no join time: an unknown age, not a
            # brand new agent.
            "age_s": _as_float(r.get("age_s")),
        }
        for r in rows
    ]
    out["in_room_count"] = len(rows)
    out["rooms"] = len({r.get("room") for r in rows})
    roster = result.get("roster")
    if roster is None:
        out["roster_configured"] = False
        return out
    workers = [w for w in roster if isinstance(w, dict)]
    out["roster_configured"] = True
    out["roster"] = [
        {
            "agent_name": w.get("agent_name") or w.get("agent_id") or None,
            "status": w.get("status") or None,
            "region": w.get("region") or None,
            "version": w.get("version") or None,
        }
        for w in workers
    ]
    out["roster_counts"] = {
        status: sum(1 for w in workers if w.get("status") == status)
        for status in ("idle", "busy", "offline")
    }
    return out


def _sfu_finding(run: RunRecord, checks: dict[str, Any]) -> dict[str, Any]:
    """The idle SFU baseline, from whichever check measured one.

    ``sfu`` and ``sfu_load`` both produce a baseline; ``source`` records which
    one this came from so the reader is not left guessing. ``quality`` is None
    when the SDK reported ``Unknown`` -- that is "no client stayed connected long
    enough to read one", not a quality reading.
    """
    got = _check_state(run, checks, "sfu")
    source = "sfu"
    if got["state"] != "ok":
        alt = _check_state(run, checks, "sfu_load")
        if alt["state"] == "ok":
            got, source = alt, "sfu_load"
        elif got["state"] == "not_requested" and alt["state"] != "not_requested":
            got, source = alt, "sfu_load"
    result = got["result"] or {}
    baseline = result.get("baseline") if isinstance(result, dict) else None
    baseline = baseline if isinstance(baseline, dict) else {}
    quality = baseline.get("quality") or None
    return {
        "state": got["state"],
        "error": got["error"],
        "source": source if got["state"] != "not_requested" else None,
        "baseline_rtt_ms": _as_float(baseline.get("rtt_ms")),
        # "Unknown" is the probe's word for "no reading", so it is not passed on
        # as if it were one.
        "quality": None if quality == "Unknown" else quality,
        # Structural, not a value: see _REPORT_LIMITS.
        "packet_loss": NOT_MEASURED,
        "target_rtt_ms": _as_float(result.get("target_rtt_ms")),
    }


def _knee_finding(
    steps: list[dict[str, Any]], knee: int | None, target_rtt_ms: float | None
) -> dict[str, Any]:
    """Resolve ``knee`` against the ramp so a null knee is never ambiguous.

    ``find_knee`` returns None for two OPPOSITE outcomes: nothing breached the
    budget, or the FIRST tier already did. A report that prints "knee: none" for
    both labels a total failure as a clean ramp. Same classification the
    dashboard's Load tab does, on rtt against the budget, exactly as find_knee
    computed it (loss plays no part -- it is not measured).
    """
    if not steps:
        return {"kind": "no_ramp", "clients": None, "breach": None}
    if target_rtt_ms is None:
        # No threshold travelled with the ramp, so its steps cannot be compared
        # against anything and the ambiguity cannot be resolved. Saying so beats
        # inventing a budget.
        return {"kind": "no_budget", "clients": knee, "breach": None}
    breach = next(
        (s for s in steps if s["rtt_ms"] is not None and s["rtt_ms"] > target_rtt_ms),
        None,
    )
    if knee is not None:
        return {"kind": "knee", "clients": knee, "breach": breach or steps[-1]}
    if breach is None:
        return {
            "kind": "all_healthy",
            "clients": max(
                (s["clients"] for s in steps if s["clients"] is not None), default=None
            ),
            "breach": None,
        }
    return {"kind": "first_tier_breached", "clients": None, "breach": breach}


def _load_finding(run: RunRecord, checks: dict[str, Any]) -> dict[str, Any]:
    """The client ramp, its knee, and the prober host's own load during it.

    The host block is not decoration: a laptop that pegged its own CPU at 25
    clients draws the same curve as an SFU that ran out of headroom, and this is
    the only thing that says which one happened. Its nulls mean "not sampled",
    never "idle", which is why ``saturated`` can be None.
    """
    got = _check_state(run, checks, "sfu_load")
    out: dict[str, Any] = {
        "state": got["state"],
        "error": got["error"],
        "ramp": [],
        "target_rtt_ms": None,
        "knee": None,
        "prober_host": None,
    }
    result = got["result"]
    if result is None:
        return out
    target = _as_float(result.get("target_rtt_ms"))
    steps: list[dict[str, Any]] = []
    for raw in result.get("ramp") or []:
        if not isinstance(raw, dict):
            continue
        rtt = _as_float(raw.get("rtt_ms"))
        quality = raw.get("quality") or None
        steps.append(
            {
                "clients": _as_int(raw.get("clients")),
                "rtt_ms": rtt,
                "quality": None if quality == "Unknown" else quality,
                # None, not False: with no budget there is nothing to be within.
                "within_budget": None
                if (target is None or rtt is None)
                else rtt <= target,
            }
        )
    out["ramp"] = steps
    out["target_rtt_ms"] = target
    out["knee"] = _knee_finding(steps, _as_int(result.get("knee")), target)
    resource = result.get("resource")
    if isinstance(resource, dict):
        per_client = resource.get("per_client") or {}
        saturated = resource.get("saturated")
        out["prober_host"] = {
            "saturated": None if saturated is None else bool(saturated),
            "cpu_peak_pct": _as_float(resource.get("cpu_peak")),
            "mem_peak_mb": _as_float(resource.get("mem_peak_mb")),
            "net_kbps_up": _as_float(resource.get("net_kbps_up")),
            "per_client_cpu_pct": _as_float(per_client.get("cpu_pct")),
            "per_client_kbps_up": _as_float(per_client.get("kbps_up")),
            "sustainable_clients": _as_int(resource.get("sustainable_n")),
        }
    return out


def _tail_finding(stats: dict[str, Any], trials: int) -> dict[str, Any] | None:
    """The slow tail, named for the statistic it actually is.

    ``summarize`` puts a ``p95`` on the wire computed from as few as one sample.
    A run places at most ``MAX_LATENCY_TRIALS`` = 3 billed calls per agent, so
    that number is the max wearing a more confident label, and this report never
    prints it as a percentile. The threshold is gates.MIN_PERCENTILE_SAMPLES, the
    same one the CLI gate names its metric after.
    """
    if trials >= gates.MIN_PERCENTILE_SAMPLES:
        p95 = _ms(stats.get("p95"))
        if p95 is not None:
            return {"statistic": "p95", "label": "p95", "value_ms": p95}
    value = _ms(stats.get("max"))
    if value is None:
        return None
    return {
        "statistic": f"max_of_{trials}",
        "label": f"max of {trials}",
        "value_ms": value,
    }


def _latency_finding(run: RunRecord, checks: dict[str, Any]) -> dict[str, Any]:
    """Reply latency per probed agent, in milliseconds.

    ``summarize`` returns 0.0 for every statistic when nothing answered, and a
    real reply cannot arrive in zero seconds, so ``trials == 0`` reports every
    number as null and the reader is told the probe measured nothing rather than
    being shown a suspiciously fast agent.
    """
    got = _check_state(run, checks, "latency")
    out: dict[str, Any] = {
        "state": got["state"],
        "error": got["error"],
        "target_ms": _as_float(run.config.get("target_ms")),
        "agents": [],
    }
    result = got["result"]
    if result is None:
        return out
    entries = []
    for raw in result.get("agents") or []:
        if not isinstance(raw, dict):
            continue
        raw_stats = raw.get("stats")
        stats: dict[str, Any] = raw_stats if isinstance(raw_stats, dict) else {}
        trials = _as_int(stats.get("trials")) or 0
        measured = trials > 0
        components = raw.get("components")
        split = None
        if isinstance(components, dict):
            split = {
                key: _ms(components.get(key))
                for key in ("eou", "stt", "stt_ttfp", "llm_ttft", "tts")
                if components.get(key) is not None
            }
        entries.append(
            {
                "agent": raw.get("agent") or None,
                "measured": measured,
                # Trials that ANSWERED. The payload does not carry the number
                # placed, so no denominator is implied.
                "trials_answered": trials,
                "avg_ms": _ms(stats.get("avg")) if measured else None,
                "min_ms": _ms(stats.get("min")) if measured else None,
                "tail": _tail_finding(stats, trials) if measured else None,
                # None means this host never saw the agent's own telemetry rows
                # (a remote collector, or an agent that is not instrumented), so
                # the per-leg split was never recorded here.
                "components_ms": split or None,
            }
        )
    out["agents"] = entries
    return out


def _report_target(livekit_url: str | None) -> dict[str, Any]:
    """What the report says it was pointed at, and how sure it is.

    The run row does NOT store the LiveKit server it probed, so the caller
    resolves it on the exporting host at export time and hands it in (the
    dashboard and the CLI resolve it the same way, and both pass None when no
    credentials are configured). That is honest and useful in the common case
    (one deployment, unchanged config) and clearly labelled for the case where it
    is not, rather than silently attributing a run to a server that may have been
    reconfigured since.
    """
    return {
        "livekit_url": livekit_url,
        # False, always: no run record has ever carried its target.
        "recorded_with_run": False,
        "resolved": "on the exporting host, at export time",
    }


def build_payload(run: RunRecord, *, livekit_url: str | None) -> dict[str, Any]:
    """The one report payload. The JSON export IS this; the HTML renders it.

    Nothing here judges the run. ``verdict`` and ``gates`` are read back out of
    what :mod:`voicegateway.livekit_diag.gates` recorded when the run executed;
    a run recorded before gates existed reports ``gates_recorded: false`` and
    keeps its stored verdict, instead of being re-judged against today's rules
    and disagreeing with the log it printed at the time.

    ``livekit_url`` is the server the exporting host resolves right now, or None
    when it cannot resolve one: it is not read off the run, because no run record
    has ever carried it.
    """
    results: dict[str, Any] = run.results if isinstance(run.results, dict) else {}
    raw_checks = results.get("checks")
    checks: dict[str, Any] = raw_checks if isinstance(raw_checks, dict) else {}
    stored_gates = results.get("gates")
    gates_recorded = isinstance(stored_gates, list)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "generated_at": _generated_at(),
        "generator": {"tool": "voicegateway", "version": _PUBLIC_VERSION},
        "run": {
            "run_id": run.run_id,
            "status": run.status,
            "checks_requested": list(run.checks),
            "config": dict(run.config),
            "created_at": run.created_at or None,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "error": run.error,
        },
        "target": _report_target(livekit_url),
        "verdict": {
            "status": run.verdict,
            "recorded": run.verdict is not None,
            "meaning": _VERDICT_MEANING.get(run.verdict or "") or None,
            "decided_by": "voicegateway.livekit_diag.gates",
        },
        "gates_recorded": gates_recorded,
        "gates": list(stored_gates) if isinstance(stored_gates, list) else None,
        "findings": {
            "agents": _agents_finding(run, checks),
            "sfu": _sfu_finding(run, checks),
            "load": _load_finding(run, checks),
            "latency": _latency_finding(run, checks),
        },
        "not_measured": list(_REPORT_LIMITS),
    }


def report_filename(run_id: str) -> str:
    """A download filename that cannot smuggle anything into the header.

    ``run_id`` reaches the dashboard from the URL path, so it is stripped to the
    characters a filename needs before it is interpolated into
    Content-Disposition. The CLI writes the same name to disk by default, so a
    report downloaded from the dashboard and one exported in CI are the same
    file under the same name.
    """
    safe = re.sub(r"[^A-Za-z0-9_-]", "", run_id)[:32] or "run"
    return f"voicegateway-diagnostics-{safe}.html"


# ---------------------------------------------------------------------------
# HTML rendering: one file, no network, readable months later
# ---------------------------------------------------------------------------

# Inline, and deliberately boring. No @font-face, no url(), no import: a font or
# an image the browser has to fetch is exactly what stops this file rendering
# from a laptop with no internet. The status colours are decoration only -- every
# status is also spelled out in words, so the report survives being printed in
# black and white or read by somebody who cannot distinguish them.
_REPORT_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 28px 64px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica,
    Arial, sans-serif;
  font-size: 15px; line-height: 1.55; color: #16181d; background: #fff;
  max-width: 980px;
}
h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -0.01em; }
h2 { font-size: 16px; margin: 34px 0 10px; padding-bottom: 6px;
     border-bottom: 1px solid #e3e6ea; }
h3 { font-size: 14px; margin: 20px 0 6px; }
p { margin: 8px 0; }
.sub { color: #5b6472; font-size: 13px; margin: 0 0 18px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.meta { border: 1px solid #e3e6ea; border-radius: 6px; padding: 14px 16px;
        margin: 18px 0; }
.meta dl { display: grid; grid-template-columns: 220px 1fr; gap: 6px 18px;
           margin: 0; font-size: 13px; }
.meta dt { color: #5b6472; }
.meta dd { margin: 0; }
.verdict { border: 2px solid #16181d; border-radius: 6px; padding: 16px 18px;
           margin: 18px 0 6px; }
.verdict .word { font-size: 26px; font-weight: 800; letter-spacing: 0.02em; }
.verdict.pass { border-color: #1f7a44; background: #f2faf5; }
.verdict.warn { border-color: #a86a00; background: #fdf7ec; }
.verdict.unknown { border-color: #4a4f5a; background: #f4f5f7; }
.verdict.fail { border-color: #a32020; background: #fdf3f3; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 13px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid #e9ebef;
         vertical-align: top; }
th { background: #f6f7f9; font-weight: 600; }
td.num, th.num { text-align: right;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.tag { display: inline-block; padding: 1px 7px; border-radius: 3px;
       font-size: 11px; font-weight: 700; letter-spacing: 0.03em;
       border: 1px solid #c9ced6; }
.tag.pass { border-color: #1f7a44; color: #1f7a44; }
.tag.warn { border-color: #a86a00; color: #a86a00; }
.tag.unknown { border-color: #4a4f5a; color: #4a4f5a; }
.tag.waived { border-color: #6a4ca8; color: #6a4ca8; }
.tag.fail { border-color: #a32020; color: #a32020; }
.nm { color: #6b7280; font-style: italic; }
.note { border-left: 3px solid #c9ced6; padding: 8px 12px; margin: 10px 0;
        background: #f8f9fb; font-size: 13px; color: #3d434d; }
.note.bad { border-left-color: #a32020; background: #fdf3f3; }
.note.warn { border-left-color: #a86a00; background: #fdf7ec; }
.note.good { border-left-color: #1f7a44; background: #f2faf5; }
ul { margin: 8px 0; padding-left: 20px; font-size: 13px; color: #3d434d; }
li { margin: 4px 0; }
footer { margin-top: 40px; padding-top: 12px; border-top: 1px solid #e3e6ea;
         font-size: 12px; color: #5b6472; }
@media print { body { padding: 0; max-width: none; } h2 { break-after: avoid; } }
"""

_STATE_TEXT = {
    "not_requested": (
        "This check was not part of the run, so nothing in this section was "
        "measured. It is absent, not clean."
    ),
    "no_result": (
        "This check was requested but recorded no result: the run did not get "
        "far enough to report one."
    ),
}


def _esc(value: Any) -> str:
    """Escape anything that goes into the document. Nothing is trusted."""
    return html.escape("" if value is None else str(value), quote=True)


def _num(value: float | None, unit: str, digits: int = 0) -> str:
    """A measured number, or the "not measured" marker. Never a fabricated 0."""
    if value is None:
        return '<span class="nm">not measured</span>'
    rendered = f"{value:,.{digits}f}"
    return f"{rendered}&nbsp;{_esc(unit)}" if unit else rendered


def _ratio_pct(value: float) -> str:
    """A 0..1 fraction as the percentage the acceptance criteria are written in.

    Precision is adaptive and trailing zeros are dropped, so 0.995 reads "99.5%",
    0.75 reads "75%" and 0.6666 reads "66.66%". Two decimal places in percent is
    enough to render every contracted threshold EXACTLY, which is the property
    that matters: a threshold shown at lower precision than the constant it came
    from is a misstatement of the contract, not a rounding.
    """
    text = f"{value * 100:.2f}".rstrip("0").rstrip(".")
    return f"{text}%"


def _gate_number(value: float | None, gate: str | None) -> str:
    """One gate's value or threshold, in the unit that gate measures in.

    Ratio gates render as percentages; everything else keeps the previous
    one-decimal rendering, because the latency and SFU gates carry milliseconds
    and a percent sign on those would be a new defect replacing an old one.

    The classification comes from :data:`gates.RATIO_GATES` rather than from the
    metric name. An unmeasured headroom gate carries no metric and a real 0.2
    threshold, so a name-based rule would still misstate the contracted 20%.
    """
    if value is None or gate not in gates.RATIO_GATES:
        return _num(value, "", 1)
    return _ratio_pct(value)


def _plain(value: Any) -> str:
    """A measured string, or the "not measured" marker."""
    if value is None or value == "":
        return '<span class="nm">not measured</span>'
    return _esc(value)


def _recorded(value: Any, suffix: str = "") -> str:
    """A run PARAMETER, or the "not recorded" marker.

    Distinct from :func:`_plain` on purpose: a threshold the run was configured
    with is not a measurement, and calling its absence "not measured" would
    misfile a missing setting as a failed reading.
    """
    if value is None or value == "":
        return '<span class="nm">not recorded</span>'
    return _esc(value) + suffix


def _state_note(finding: dict[str, Any]) -> str | None:
    """The banner for a check that produced nothing, or None when it produced."""
    state = finding.get("state")
    if state == "errored":
        return (
            '<div class="note bad">This check did not complete: '
            f"{_esc(finding.get('error'))}. Nothing below it was measured.</div>"
        )
    text = _STATE_TEXT.get(str(state))
    if text is not None:
        return f'<div class="note">{_esc(text)}</div>'
    return None


def _render_meta(payload: dict[str, Any]) -> str:
    run = payload["run"]
    target = payload["target"]
    config = run.get("config") or {}
    target_ms = _as_float(config.get("target_ms"))
    url = target.get("livekit_url")
    url_cell = (
        f'<span class="mono">{_esc(url)}</span>'
        if url
        else '<span class="nm">not recorded: no LiveKit credentials are '
        "configured on the host that exported this report</span>"
    )
    rows = [
        ("Run id", f'<span class="mono">{_esc(run["run_id"])}</span>'),
        ("Run status", _plain(run.get("status"))),
        ("Queued", _plain(run.get("created_at"))),
        ("Started", _plain(run.get("started_at"))),
        ("Ended", _plain(run.get("ended_at"))),
        (
            "Checks requested",
            _esc(", ".join(run.get("checks_requested") or []))
            or '<span class="nm">none recorded</span>',
        ),
        (
            "LiveKit server",
            url_cell
            + '<br><span class="nm">read on the exporting host at export time: '
            "the run record does not store the server it probed</span>",
        ),
        (
            "Reply-latency target",
            _recorded(None if target_ms is None else f"{target_ms:,.0f} ms"),
        ),
        (
            "Trials per agent",
            _recorded(config.get("trials"), " (each one a real, billed call)"),
        ),
        (
            "Ramp tiers",
            _recorded(", ".join(str(n) for n in (config.get("ramp") or []))),
        ),
        ("Report generated", _esc(payload["generated_at"])),
        (
            "Generated by",
            f"voicegateway {_esc(payload['generator']['version'])}"
            f" &middot; report schema v{payload['schema_version']}",
        ),
    ]
    body = "".join(f"<dt>{label}</dt><dd>{value}</dd>" for label, value in rows)
    return f'<div class="meta"><dl>{body}</dl></div>'


def _render_verdict(payload: dict[str, Any]) -> str:
    verdict = payload["verdict"]
    status = verdict.get("status")
    run = payload["run"]
    if status is None:
        detail = run.get("error") or "the reason was not recorded"
        return (
            '<div class="verdict unknown"><div class="word">NO VERDICT</div>'
            "<p>This run recorded no verdict, so it demonstrates nothing either "
            f"way: {_esc(detail)}.</p></div>"
        )
    css = str(status).lower()
    css = css if css in ("pass", "warn", "unknown", "fail") else "unknown"
    meaning = verdict.get("meaning") or (
        "This status was recorded by the run and is reproduced verbatim; this "
        "build does not know how to explain it."
    )
    return (
        f'<div class="verdict {css}"><div class="word">{_esc(status)}</div>'
        f"<p>{_esc(meaning)}</p>"
        '<p class="nm">The verdict is the worst gate below '
        "(PASS &lt; WAIVED &lt; WARN &lt; UNKNOWN &lt; FAIL), decided when the "
        "run "
        "executed, not when this report was exported.</p></div>"
    )


def _render_gates(payload: dict[str, Any]) -> str:
    if not payload.get("gates_recorded"):
        return (
            '<h2>Gates</h2><div class="note">This run recorded no per-gate '
            "detail. It predates per-gate provenance in this deployment, so only "
            "the overall verdict above survives from it. It has deliberately NOT "
            "been re-judged at export time: that would produce a report "
            "disagreeing with what the run itself reported.</div>"
        )
    stored = payload["gates"] or []
    rows = []
    for gate in stored:
        if not isinstance(gate, dict):
            continue
        status = str(gate.get("status") or "UNKNOWN")
        css = (
            status.lower()
            if status.lower() in ("pass", "waived", "warn", "unknown", "fail")
            else "unknown"
        )
        subject = gate.get("subject")
        # The step is part of the row's identity, not decoration. Without it a
        # seven-step run rendered seven rows reading
        # "sura-sip-01/node-exporter/file_descriptors" with nothing to tell them
        # apart, and a fleet run multiplies that by the node count. Suppressed
        # when it merely repeats the subject, which is the establishment gate,
        # whose subject IS the step.
        step = gate.get("step")
        identity = str(gate.get("gate") or "")
        if step and step != subject:
            identity = f"{_esc(identity)}<br>{_esc(step)}"
        else:
            identity = _esc(identity)
        value = _as_float(gate.get("value"))
        threshold = _as_float(gate.get("threshold"))
        rows.append(
            f'<tr><td><span class="tag {css}">{_esc(status)}</span></td>'
            f'<td class="mono">{identity}'
            + (f"<br>{_esc(subject)}" if subject else "")
            + f"</td><td>{_esc(gate.get('detail'))}</td>"
            # A gate can legitimately carry no metric: agents_listing asserts the
            # server answered, and an UNKNOWN gate never got as far as a number.
            # "not recorded" says that; calling a missing NAME "not measured"
            # would file it as a failed reading.
            f'<td class="mono">{_recorded(gate.get("metric"))}</td>'
            f'<td class="num">{_gate_number(value, gate.get("gate"))}</td>'
            f'<td class="num">{_gate_number(threshold, gate.get("gate"))}</td></tr>'
        )
    if not rows:
        if stored:
            return (
                "<h2>Gates</h2>"
                f'<div class="note">This run recorded {len(stored)} gate '
                "entries, and none of them is in a shape this build can read. "
                "They are not reproduced rather than guessed at; read the run "
                "payload directly.</div>"
            )
        return (
            "<h2>Gates</h2>"
            '<div class="note">This run evaluated no gate at all: no check '
            "produced anything a gate knows how to read.</div>"
        )
    return (
        "<h2>Gates</h2><table><thead><tr><th>Status</th><th>Gate</th>"
        '<th>What it found</th><th>Metric</th><th class="num">Value</th>'
        '<th class="num">Threshold</th></tr></thead><tbody>'
        + "".join(rows)
        + "</tbody></table>"
        '<div class="note">A gate reading UNKNOWN did not evaluate. It is not a '
        "pass with a caveat: nothing was compared against anything, and the "
        "value it would have reported is unknown.</div>"
    )


def _render_agents(finding: dict[str, Any]) -> str:
    out = ["<h2>Agents</h2>"]
    note = _state_note(finding)
    if note is not None:
        return "".join(out) + note
    rows = finding.get("in_room") or []
    out.append(
        f"<p>{finding.get('in_room_count')} agent(s) in "
        f"{finding.get('rooms')} room(s) at the moment this check ran.</p>"
    )
    if rows:
        body = "".join(
            "<tr>"
            f"<td>{_plain(r.get('agent_name'))}</td>"
            f'<td class="mono">{_plain(r.get("room"))}</td>'
            f"<td>{_plain(r.get('state'))}</td>"
            f'<td class="num">{_plain(r.get("humans"))}</td>'
            f'<td class="num">{_num(r.get("age_s"), "s")}</td></tr>'
            for r in rows
        )
        out.append(
            "<table><thead><tr><th>Agent</th><th>Room</th><th>State</th>"
            '<th class="num">Humans</th><th class="num">Age</th></tr></thead>'
            f"<tbody>{body}</tbody></table>"
        )
    else:
        out.append(
            '<div class="note">No agent was in a room when this check ran. '
            "LiveKit reports a worker only once it has JOINED a room, so an idle "
            "worker that is running perfectly looks identical to no worker at "
            "all here. The heartbeat roster is what tells them apart.</div>"
        )
    configured = finding.get("roster_configured")
    if configured is False:
        out.append(
            '<h3>Registered workers (heartbeat roster)</h3><div class="note">'
            "No collector was configured for this run, so the idle/registered "
            "fleet was never asked and is not measured here. That is different "
            "from a fleet of zero workers.</div>"
        )
    elif configured:
        workers = finding.get("roster") or []
        counts = finding.get("roster_counts") or {}
        out.append(
            f"<h3>Registered workers (heartbeat roster)</h3><p>{len(workers)} "
            f"registered &middot; {counts.get('idle', 0)} idle &middot; "
            f"{counts.get('busy', 0)} busy &middot; "
            f"{counts.get('offline', 0)} offline.</p>"
        )
        if workers:
            body = "".join(
                f"<tr><td>{_plain(w.get('agent_name'))}</td>"
                f"<td>{_plain(w.get('status'))}</td>"
                f"<td>{_plain(w.get('region'))}</td>"
                f'<td class="mono">{_plain(w.get("version"))}</td></tr>'
                for w in workers
            )
            out.append(
                "<table><thead><tr><th>Worker</th><th>Status</th><th>Region</th>"
                f"<th>Version</th></tr></thead><tbody>{body}</tbody></table>"
            )
        else:
            out.append(
                '<div class="note">The collector was configured but reported no '
                "worker: either none has sent a heartbeat yet, or it could not "
                "be reached on this run.</div>"
            )
    return "".join(out)


def _render_sfu(finding: dict[str, Any]) -> str:
    out = ["<h2>SFU baseline</h2>"]
    note = _state_note(finding)
    if note is not None:
        return "".join(out) + note
    source = finding.get("source")
    measured_by = (
        f' (measured by the <span class="mono">{_esc(source)}</span> check)'
        if source
        else ""
    )
    out.append(
        f"<p>Two synthetic clients, idle, talking through the SFU{measured_by}.</p>"
    )
    out.append(
        "<table><tbody>"
        "<tr><td>Round trip through the SFU (data channel)</td>"
        f'<td class="num">{_num(finding.get("baseline_rtt_ms"), "ms", 1)}</td></tr>'
        "<tr><td>Connection quality (SDK)</td>"
        f'<td class="num">{_plain(finding.get("quality"))}</td></tr>'
        "<tr><td>Packet loss</td>"
        '<td class="num"><span class="nm">not measured</span></td></tr>'
        "<tr><td>Budget the ramp was compared against</td>"
        f'<td class="num">{_num(finding.get("target_rtt_ms"), "ms")}</td></tr>'
        "</tbody></table>"
    )
    out.append(
        '<div class="note">Packet loss is not measured and no loss figure is '
        "reported anywhere in this document: the probe reads a hardcoded "
        "placeholder, not the connection. Quality is the coarse signal that is "
        "real, and a quality of &ldquo;not measured&rdquo; means no client "
        "stayed connected long enough to read one.</div>"
    )
    return "".join(out)


def _budget_cell(within: Any) -> str:
    """A ramp tier against the budget. None is a third answer, not a failure.

    None means no rtt budget travelled with the ramp, so the tier was never
    compared to anything: rendering it as "over" or "within" would report a
    comparison that did not happen.
    """
    if within is True:
        return "within budget"
    if within is False:
        return "over budget"
    return '<span class="nm">no budget to compare against</span>'


def _render_knee(knee: dict[str, Any], target: float | None) -> str:
    kind = knee.get("kind")
    breach = knee.get("breach") or {}
    budget = _num(target, "ms")
    if kind == "no_ramp":
        return (
            '<div class="note">This run measured no ramp, so there is no '
            "capacity curve and no knee. Nothing is inferred from an absent "
            "measurement.</div>"
        )
    if kind == "no_budget":
        return (
            '<div class="note">No rtt budget travelled with this ramp, so its '
            "tiers cannot be compared against anything and the knee cannot be "
            "resolved. A knee of &ldquo;none&rdquo; means two opposite things "
            "(nothing broke, or the first tier broke), and without the budget "
            "this report cannot say which.</div>"
        )
    if kind == "knee":
        return (
            f'<div class="note warn">Knee at {_esc(knee.get("clients"))} clients: '
            f"every tier up to {_esc(knee.get('clients'))} stayed inside the "
            f"{budget} budget, and {_esc(breach.get('clients'))} clients broke it "
            f"at {_num(breach.get('rtt_ms'), 'ms', 1)}.</div>"
        )
    if kind == "all_healthy":
        return (
            '<div class="note good">No knee inside this ramp: every tier up to '
            f"{_esc(knee.get('clients'))} clients stayed under the {budget} "
            "budget. That is a floor on capacity, not a ceiling: the ramp "
            "stopped there, it did not find a limit.</div>"
        )
    return (
        '<div class="note bad">No healthy tier in this ramp: the smallest tier '
        f"({_esc(breach.get('clients'))} clients) already exceeded the {budget} "
        f"budget at {_num(breach.get('rtt_ms'), 'ms', 1)}. There is no knee "
        "because nothing passed, which is the opposite of a clean ramp.</div>"
    )


def _render_load(finding: dict[str, Any]) -> str:
    out = ["<h2>SFU load ramp</h2>"]
    note = _state_note(finding)
    if note is not None:
        return "".join(out) + note
    steps = finding.get("ramp") or []
    knee = finding.get("knee") or {"kind": "no_ramp"}
    out.append(_render_knee(knee, finding.get("target_rtt_ms")))
    if steps:
        body = "".join(
            f'<tr><td class="num">{_plain(s.get("clients"))}</td>'
            f'<td class="num">{_num(s.get("rtt_ms"), "ms", 1)}</td>'
            f"<td>{_plain(s.get('quality'))}</td>"
            f"<td>{_budget_cell(s.get('within_budget'))}</td></tr>"
            for s in steps
        )
        out.append(
            '<table><thead><tr><th class="num">Clients</th>'
            '<th class="num">Round trip</th><th>Quality</th>'
            f"<th>Against budget</th></tr></thead><tbody>{body}</tbody></table>"
        )
        out.append(
            '<div class="note">Round trip and quality are the only measured '
            "columns, and that round trip is the prober&rsquo;s own message "
            "through the SFU data channel and back, not the latency a caller "
            "hears an agent answer with.</div>"
        )
    host = finding.get("prober_host")
    out.append("<h3>Prober host during the ramp</h3>")
    if host is None:
        out.append(
            '<div class="note">The prober&rsquo;s own CPU, memory and uplink '
            "were not sampled on this run, so its numbers cannot be attributed "
            "to the SFU rather than to this machine.</div>"
        )
        return "".join(out)
    saturated = host.get("saturated")
    if saturated is True:
        out.append(
            '<div class="note bad">This host saturated during the ramp. The '
            "curve above describes THIS machine, not the SFU: re-run from a "
            "larger host, or with lower tiers, before reading the knee as a "
            "server limit.</div>"
        )
    elif saturated is False:
        out.append(
            '<div class="note good">This host did not saturate, so the ramp '
            "above is a measurement of the SFU rather than of the prober.</div>"
        )
    else:
        out.append(
            '<div class="note warn">Saturation is unknown for this run: no '
            "usable CPU sample was taken, so nobody can say whether the prober "
            "or the SFU was the limit. It is not reported as &ldquo;not "
            "saturated&rdquo;, because that was never measured.</div>"
        )
    out.append(
        "<table><tbody>"
        f'<tr><td>Peak CPU on the prober</td><td class="num">'
        f"{_num(host.get('cpu_peak_pct'), '%', 1)}</td></tr>"
        f'<tr><td>Peak memory (RSS)</td><td class="num">'
        f"{_num(host.get('mem_peak_mb'), 'MB')}</td></tr>"
        f'<tr><td>Uplink during the ramp</td><td class="num">'
        f"{_num(host.get('net_kbps_up'), 'kbps')}</td></tr>"
        f'<tr><td>CPU per client</td><td class="num">'
        f"{_num(host.get('per_client_cpu_pct'), '%', 2)}</td></tr>"
        f'<tr><td>Uplink per client</td><td class="num">'
        f"{_num(host.get('per_client_kbps_up'), 'kbps', 1)}</td></tr>"
        f'<tr><td>Clients this host sustains</td><td class="num">'
        f"{_plain(host.get('sustainable_clients'))}</td></tr>"
        "</tbody></table>"
    )
    return "".join(out)


def _render_latency(finding: dict[str, Any]) -> str:
    out = ["<h2>Reply latency (real calls)</h2>"]
    note = _state_note(finding)
    if note is not None:
        return "".join(out) + note
    agents = finding.get("agents") or []
    if not agents:
        out.append(
            '<div class="note">No agent was in a room to call, so no reply '
            "latency was measured. The run probes the agents LiveKit reported; "
            "it never invents a target to dial.</div>"
        )
        return "".join(out)
    target = finding.get("target_ms")
    for agent in agents:
        out.append(f"<h3>{_plain(agent.get('agent'))}</h3>")
        if not agent.get("measured"):
            out.append(
                '<div class="note bad">Nothing was measured for this agent: no '
                "trial produced a reply. The end-to-end time is reported as not "
                "measured rather than as a zero, which would read as an instant "
                "answer.</div>"
            )
            continue
        tail = agent.get("tail") or {}
        split = agent.get("components_ms")
        rows = [
            (
                f"Slowest reply ({_esc(tail.get('label') or 'tail')}, end to end)",
                _num(tail.get("value_ms"), "ms"),
            ),
            ("Average reply", _num(agent.get("avg_ms"), "ms")),
            ("Fastest reply", _num(agent.get("min_ms"), "ms")),
            ("Trials that answered", _plain(agent.get("trials_answered"))),
            ("Target this run was measured against", _num(target, "ms")),
        ]
        if split:
            labels = {
                "eou": "Turn detection (end of utterance)",
                "stt": "STT",
                "stt_ttfp": "STT time to first partial",
                "llm_ttft": "LLM time to first token",
                "tts": "TTS",
            }
            rows.extend(
                (labels.get(key, key), _num(value, "ms"))
                for key, value in split.items()
            )
        body = "".join(
            f'<tr><td>{label}</td><td class="num">{value}</td></tr>'
            for label, value in rows
        )
        out.append(f"<table><tbody>{body}</tbody></table>")
        if not split:
            out.append(
                '<div class="note">The STT/LLM/TTS split was not recorded for '
                "this agent: the split is read back out of the rows the agent "
                "itself writes, and this host never saw any for the probe room "
                "(an uninstrumented agent, or one reporting to a remote "
                "collector).</div>"
            )
        trials = agent.get("trials_answered") or 0
        if tail.get("statistic", "").startswith("max_of"):
            out.append(
                f'<div class="note">{trials} sample(s) is below the '
                f"{gates.MIN_PERCENTILE_SAMPLES} a percentile needs, so the "
                f"headline is the max of {trials} and not a p95. A run places at "
                "most three calls per agent, because each one is billed.</div>"
            )
    return "".join(out)


def _render_limits(payload: dict[str, Any]) -> str:
    """The limits section, describing the run this payload actually came from.

    Shared by both reports, so the preamble is chosen from the payload's kind
    rather than assumed. It described every run as "a diagnostics run", which in
    a load report is a reader's first hint that they were sent output from a
    different tool.
    """
    items = "".join(f"<li>{_esc(item)}</li>" for item in payload["not_measured"])
    subject = (
        "A load run measures a fleet from outside, through a generator placing "
        "real calls"
        if payload.get("kind") == LOAD_REPORT_KIND
        else "A diagnostics run is a single-vantage snapshot"
    )
    # The run's own gaps are a DIFFERENT claim from the structural ones and are
    # rendered apart from them. "Nothing can measure this" and "nothing measured
    # it this time" send a reader to two different places, and merging them into
    # one list makes a fixable gap look permanent and a permanent one look like
    # this run's bad luck.
    run_limits = payload.get("run_limitations") or []
    extra = ""
    if run_limits:
        entries = "".join(f"<li>{_esc(item)}</li>" for item in run_limits)
        extra = (
            "<h3>...and what THIS run did not measure</h3>"
            "<p>These are gaps in the artifacts this report was built from, not "
            "limits of the system. A later run can close them.</p>"
            f"<ul>{entries}</ul>"
        )
    return (
        "<h2>What this report does not measure</h2>"
        "<p>Read this section before acting on anything above. "
        f"{subject}, and these are its structural limits, not this "
        "run&rsquo;s bad luck.</p>"
        f"<ul>{items}</ul>{extra}"
    )


def render_html(payload: dict[str, Any]) -> str:
    """Render ``payload`` as ONE self-contained HTML document.

    Self-contained is a hard requirement, not a nicety: this file is handed to
    somebody outside the deployment and opened from disk, possibly offline,
    possibly years later. There is no <script>, no <link>, no <img>, no external
    font and no url() anywhere in it -- the CSS is inline and the only content is
    text. ``test_diagnostics_report.py`` (from the endpoint) and
    ``test_livekit_report_cli.py`` (from the file the CLI writes) both assert
    each of those, because it is the kind of property a later edit breaks
    silently by pasting in a chart library.
    """
    findings = payload["findings"]
    run_id = _esc(payload["run"]["run_id"])
    body = "".join(
        [
            "<h1>LiveKit diagnostics run report</h1>",
            f'<p class="sub">VoiceGateway &middot; run '
            f'<span class="mono">{run_id}</span></p>',
            _render_verdict(payload),
            _render_meta(payload),
            _render_gates(payload),
            _render_agents(findings["agents"]),
            _render_sfu(findings["sfu"]),
            _render_load(findings["load"]),
            _render_latency(findings["latency"]),
            _render_limits(payload),
            "<footer>Generated by voicegateway "
            f"{_esc(payload['generator']['version'])} on "
            f"{_esc(payload['generated_at'])} &middot; report schema v"
            f"{payload['schema_version']} ({_esc(payload['kind'])}). This file is "
            "self-contained: it loads nothing over the network and reads the same "
            "offline.</footer>",
        ]
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>LiveKit diagnostics report {run_id}</title>"
        f"<style>{_REPORT_CSS}</style></head><body>{body}</body></html>\n"
    )


# ---------------------------------------------------------------------------
# Load-test report
# ---------------------------------------------------------------------------
#
# A second REPORT, not a second report generator. It shares this module's
# escaping, CSS and self-containment rules on purpose: those are the properties
# a later edit breaks silently, and one copy of them is the only way they stay
# true for both documents.
#
# It is a distinct payload because it answers a different question. A
# diagnostics run is this host probing a deployment. A load run is an EXTERNAL
# generator placing calls, correlated afterwards with what the fleet was doing.
# Forcing both into one schema would mean a consumer could not tell which one it
# was holding.

#: Versions the load-test payload independently of the diagnostics one. They
#: change for different reasons and pinning them together would force a version
#: bump on one every time the other moved.
LOAD_REPORT_SCHEMA_VERSION = 1

#: What ``kind`` a consumer matches on to tell the two exports apart.
LOAD_REPORT_KIND = "voicegateway.loadtest.run_report"

#: Provenance values. Derived from evidence, never asserted: a payload is
#: ``measured`` only when the run it came from carries the checksum of a real
#: artifact, so nothing can claim measured-ness without holding the bytes.
PROVENANCE_MEASURED = "measured"
PROVENANCE_SYNTHETIC = "synthetic"

#: Stamped as the FIRST VISIBLE ELEMENT of any HTML whose provenance is not
#: measured. First, not in a footer: somebody scrolling to the numbers must hit
#: this before they hit anything that looks like a result.
SYNTHETIC_STAMP = "SYNTHETIC DATA: NOT A DELIVERABLE"

#: What a load-test report structurally cannot tell you, on top of the shared
#: limits above. Every entry here is a thing nothing in this system scrapes, and
#: a limits list that goes quiet as coverage grows reads as a clean bill of
#: health rather than as an unchanged gap.
_LOAD_REPORT_LIMITS = [
    "RTP-port headroom IS measured, from the media port range's own occupancy "
    "against its configured size. The range size is a DECLARED configuration "
    "value rather than a measurement, so a node publishing occupancy without it "
    "reports unmeasured rather than being divided by a guess.",
    "Network headroom is measured against a DECLARED baseline, not a measured "
    "capacity. The ENA driver reports no link speed, so there is no capacity to "
    "measure; the denominator is the instance type's published baseline, "
    "supplied by an operator at import. The instance can burst above it, so that "
    "figure is a floor rather than a ceiling and the ratio over-reads rather "
    "than under-reads. Inbound and outbound are judged separately because the "
    "hypervisor maintains separate credit buckets.",
    "Packets-per-second headroom is NOT measured and never will be. No "
    "per-instance-type PPS allowance is published by anyone, including AWS, so "
    "there is no denominator to divide by. PPS saturation is still detected as "
    "an EVENT by the allowance gate; what cannot be quantified is how much "
    "headroom remained.",
    "Per-call packet loss is NOT measured. No server-side surface attributes loss "
    "to one participant's leg, so no loss figure appears per call anywhere in "
    "this report.",
    "Node samples are correlated to a test by TIME WINDOW OVERLAP. They say what "
    "the fleet was doing while a test ran, never that a node served any "
    "particular call: there is no per-call identity at that layer.",
    "Peak CPU and memory are the WORST node's peak over the window, not a fleet "
    "average. A tier that looks healthy on average can contain one node that "
    "breached.",
    "The calls-per-node figure is only meaningful if the generator actually "
    "reached each step. A ramp holding its arrival rate fixed while raising the "
    "target concurrency plateaus, and that plateau is the generator's ceiling "
    "rather than the node's.",
    "Every call here was placed from ONE vantage point: the host that ran the "
    "generator. The establishment rate is what that host achieved against this "
    "deployment over that path, not what a caller on another network would see.",
]


#: A sha256 digest, lowercase hex. The SHAPE is checked, not merely presence:
#: a truthy string is not a checksum, and "TODO" or "n/a" in that column would
#: otherwise read as measured and SUPPRESS the synthetic stamp. Nothing on the
#: import path can produce such a value, but the docstring below promises that
#: measured-ness cannot be asserted without the artifact, and a bare truthiness
#: test does not keep that promise against a hand-written row.
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _provenance_of(run: dict[str, Any]) -> str:
    """``measured`` only when a real artifact was hashed.

    Derived from the checksum rather than read from a flag, so a future writer
    cannot assert measured-ness without the artifact behind it. The value must
    look like a sha256 digest; anything else is treated as no checksum at all,
    because a column holding "pending" is not evidence of anything.
    """
    checksum = run.get("artifact_sha256")
    if isinstance(checksum, str) and _SHA256_RE.fullmatch(checksum.strip().lower()):
        return PROVENANCE_MEASURED
    return PROVENANCE_SYNTHETIC


def _load_test_row(test: dict[str, Any]) -> dict[str, Any]:
    """One test's five columns, plus what was correlated to its window.

    Every value passes through unchanged, including None. A caller rendering
    this must show absence AS absence: a 0 in ``peak_concurrency`` is a test
    that carried no calls, which is a different claim from "not measured".
    """
    attempted = _as_int(test.get("attempted_calls"))
    succeeded = _as_int(test.get("succeeded_calls"))
    # Computed here from the counts rather than stored beside them, so there is
    # never a rate that can disagree with the numbers it came from.
    established = (
        succeeded / attempted
        if attempted is not None and succeeded is not None and attempted > 0
        else None
    )
    started, ended = test.get("started_at_ms"), test.get("ended_at_ms")
    return {
        "name": test.get("name"),
        "sequence": test.get("sequence"),
        "target_concurrency": _as_int(test.get("target_concurrency")),
        "peak_concurrency": _as_int(test.get("peak_concurrency")),
        "duration_ms": (
            ended - started if started is not None and ended is not None else None
        ),
        "attempted_calls": attempted,
        "succeeded_calls": succeeded,
        "failed_calls": _as_int(test.get("failed_calls")),
        "establishment_ratio": established,
        "failures_by_cause": {
            cause: _as_int(test.get(f"failed_{cause}"))
            for cause in (
                "timeout",
                "unexpected_sip",
                "transport_error",
                "parse_error",
                "scenario_error",
                "cancelled",
            )
            # Absent, not zero: a cause the artifacts did not carry is a gap in
            # the breakdown, not a claim that it never happened.
            if test.get(f"failed_{cause}") is not None
        },
        "peak_cpu_utilisation": _as_float(test.get("peak_cpu_utilisation")),
        "peak_memory_utilisation": _as_float(test.get("peak_memory_utilisation")),
        "node_samples_in_window": _as_int(test.get("node_samples_in_window")),
        "rtp_packets_sent": _as_int(test.get("rtp_packets_sent")),
        "rtp_packets_received": _as_int(test.get("rtp_packets_received")),
    }


def _load_verdict(gate_results: list[dict[str, Any]] | None) -> dict[str, Any]:
    """The run verdict, derived from the gates this payload already carries.

    Derived rather than passed in, so the verdict and the gate table can never
    disagree: they are the same list read twice.

    ``None`` gates means nothing was gated at all, which renders as NO VERDICT.
    An EMPTY list is different and is deliberately not a pass: it means gating
    ran and produced nothing, which is a run that evaluated nothing. That is the
    same rule :func:`gates.verdict` applies, and it matters because
    ``worst_status([])`` returns PASS on its own.
    """
    if gate_results is None:
        return {
            "status": None,
            "recorded": False,
            "meaning": None,
            "decided_by": "voicegateway.livekit_diag.gates",
        }
    status = (
        gates.worst_status(str(g.get("status")) for g in gate_results)
        if gate_results
        else gates.UNKNOWN
    )
    return {
        "status": status,
        "recorded": True,
        "meaning": _VERDICT_MEANING.get(status),
        "decided_by": "voicegateway.livekit_diag.gates",
    }


def build_load_payload(
    *,
    run: dict[str, Any],
    tests: list[dict[str, Any]],
    gate_results: list[dict[str, Any]] | None = None,
    capacity: dict[str, Any] | None = None,
    appendix: dict[str, list[dict[str, str]]] | None = None,
    limitations: list[str] | None = None,
    scope_exclusions: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The load-test report payload. The JSON export IS this; the HTML renders it.

    SYNCHRONOUS, like :func:`build_payload`. Nothing here judges: gate verdicts
    arrive already decided, so the report cannot re-judge a run against rules
    that changed after it executed and then disagree with the log it printed.

    ``capacity`` is optional because the calls-per-node figure it rests on is
    frequently not derivable, and a report with an honest gap in it is worth
    more than one with a plausible number in that gap.
    """
    provenance = _provenance_of(run)
    return {
        "schema_version": LOAD_REPORT_SCHEMA_VERSION,
        "kind": LOAD_REPORT_KIND,
        "generated_at": _generated_at(),
        "generator": {"tool": "voicegateway", "version": _PUBLIC_VERSION},
        # Top-level, not buried in the run block: a consumer must not have to go
        # looking to find out whether these numbers describe anything real.
        "data_provenance": provenance,
        "provenance_basis": (
            "the source run carries the checksum of a real artifact"
            if provenance == PROVENANCE_MEASURED
            # Deliberately narrow. The old wording claimed the run carried NO
            # checksum, which is false (one is computed on every import and
            # kept in the notes), and that the numbers came from fixtures,
            # which nothing knows: real artifacts imported without --captured
            # land here too. The only true claim is that nobody attested them.
            else "nobody declared these artifacts as captured from a real run, "
            "so nothing here may be read as measured, whatever it was built from"
        ),
        "run": {
            "id": run.get("id"),
            "label": run.get("label"),
            "project": run.get("project"),
            "tool": run.get("tool"),
            "tool_version": run.get("tool_version"),
            "artifact_schema_version": run.get("artifact_schema_version"),
            "artifact_sha256": run.get("artifact_sha256"),
            "started_at_ms": run.get("started_at_ms"),
            "ended_at_ms": run.get("ended_at_ms"),
        },
        "tests": [_load_test_row(t) for t in tests],
        # Derived from the gates below, so the two exports cannot disagree.
        "verdict": _load_verdict(gate_results),
        "gates_recorded": gate_results is not None,
        "gates": list(gate_results) if gate_results is not None else None,
        "capacity": capacity,
        # What it takes to run this again. Item-by-item cited; see
        # :func:`appendix_entry`.
        "appendix": appendix,
        # _REPORT_LIMITS is the PROBE's list and is deliberately absent. Its
        # entries describe a prober's data-channel round trip, billed trial
        # calls per agent and an agents check, none of which exists in a SIP
        # load test, and a client reading them concludes they were sent output
        # from a different tool. The one that IS true of a load run, the single
        # vantage point, is restated above in load terms; the packet-loss one is
        # already covered above with the reason that actually applies here.
        "not_measured": list(_LOAD_REPORT_LIMITS),
        # What THIS RUN's artifacts could not answer, as opposed to what the
        # system structurally never measures. The two are different claims and a
        # reader needs both to tell "nothing can measure this" from "nothing
        # measured it this time". Printed at import and carried nowhere until
        # now, so the only person who ever saw them was whoever ran the import.
        "run_limitations": list(limitations or []),
        # Parts of a contracted criterion this system cannot evaluate AT ALL.
        # Distinct from not_measured, which is what the report structurally does
        # not tell you, and from run_limitations, which is what these artifacts
        # could not answer. An exclusion says the requirement is not covered.
        #
        # Carried top-level and rendered beside the verdict rather than in a
        # footnote: removing eighteen UNKNOWN gate rows can move a verdict off
        # UNKNOWN, and a headline that improves while the disclosure shrinks is
        # the outcome that must not happen.
        "scope_exclusions": dict(scope_exclusions or {}),
    }


def _render_stamp(payload: dict[str, Any]) -> str:
    """The synthetic banner, or nothing at all when the run was measured."""
    if payload.get("data_provenance") == PROVENANCE_MEASURED:
        return ""
    return (
        f'<div class="stamp"><strong>{_esc(SYNTHETIC_STAMP)}</strong>'
        f"<p>{_esc(payload.get('provenance_basis') or '')}</p></div>"
    )


def _render_load_tests(payload: dict[str, Any]) -> str:
    """The per-test table: the five columns a load-test report is owed."""
    rows = []
    for test in payload["tests"]:
        causes = test["failures_by_cause"]
        breakdown = (
            ", ".join(f"{k} {v}" for k, v in sorted(causes.items()))
            if causes
            else "not measured"
        )
        rows.append(
            "<tr>"
            f"<td>{_esc(test['name'])}</td>"
            f"<td>{_recorded(test['peak_concurrency'])}</td>"
            f"<td>{_duration_cell(test['duration_ms'])}</td>"
            f"<td>{_ratio_cell(test['establishment_ratio'])}</td>"
            f"<td>{_pct_cell(test['peak_cpu_utilisation'])}</td>"
            f"<td>{_pct_cell(test['peak_memory_utilisation'])}</td>"
            f"<td>{_recorded(test['failed_calls'])}</td>"
            f"<td>{_esc(breakdown)}</td>"
            "</tr>"
        )
    return (
        "<h2>Per test</h2>"
        "<table><thead><tr><th>Test</th><th>Peak concurrency</th>"
        "<th>Duration</th><th>Established</th><th>Peak CPU</th>"
        "<th>Peak memory</th><th>Failed</th><th>By cause</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        "<p class='sub'>Peak CPU and memory are the worst node&rsquo;s peak over "
        "each test&rsquo;s window, correlated by time overlap. They describe the "
        "fleet during the test, not any particular call.</p>"
    )


def _ms_to_minutes(value: Any) -> float | None:
    ms = _as_float(value)
    return None if ms is None else ms / 60_000.0


#: Below this, a duration is shown in seconds. A ramp step is commonly a minute
#: or two, and one decimal of a minute cannot tell 62 seconds from 66: both read
#: "1.1 min". A reader comparing this against their own timing needs the unit
#: the run was configured in.
_SECONDS_BELOW_MS = 10 * 60 * 1000


def _duration_cell(value: Any) -> str:
    """A test's wall duration, in a unit that survives being checked."""
    ms = _as_float(value)
    if ms is None:
        return _num(None, "", 0)
    if ms < _SECONDS_BELOW_MS:
        return _num(ms / 1000.0, "s", 1)
    return _num(ms / 60_000.0, "min", 1)


def _ratio_cell(value: float | None) -> str:
    """A ratio as a percentage, or the words. Never a bare 0 for an absence."""
    return "not measured" if value is None else f"{value * 100:.3f}%"


def _pct_cell(value: float | None) -> str:
    return "not measured" if value is None else f"{value * 100:.1f}%"


def _render_capacity(payload: dict[str, Any]) -> str:
    """The node count per tier, or the reason there is none."""
    capacity = payload.get("capacity")
    if not capacity:
        # NOTHING RAN THE DERIVATION. Distinct from a derivation that ran and
        # refused, which is the branch below and carries a reason. Saying "not
        # derivable from this run" here would claim an attempt was made and
        # blame the data for a gap in the caller.
        return (
            "<h2>Capacity</h2><p>No capacity table, and none was attempted: "
            "whoever built this payload supplied no capacity block, so nothing "
            "here is a statement about the run. Sizing without a derivation "
            "would mean inventing the one number the whole table rests on.</p>"
        )
    if capacity.get("calls_per_node") is None:
        # The derivation RAN and refused. The reason is the whole value of this
        # branch, so it is never summarised away.
        return (
            "<h2>Capacity</h2><p>No capacity table. The calls-per-node figure "
            "was not derivable, so sizing would mean inventing the one number "
            "the whole table rests on.</p>"
            # The reason is a sentence fragment from the derivation and begins
            # lowercase. Concatenating it after a full stop read as a typo in a
            # document somebody is paying for, so it gets its own line and its
            # own label.
            f'<p class="sub">Why: {_esc(capacity.get("reason") or "not stated")}</p>'
        )
    rows = "".join(
        "<tr>"
        f"<td>{_esc(tier['target_concurrency'])}</td>"
        f"<td>{_esc(tier['nodes_for_load'])}</td>"
        f"<td>{_esc(tier['spare_nodes'])}</td>"
        f"<td><strong>{_esc(tier['nodes'])}</strong></td>"
        "</tr>"
        for tier in capacity.get("tiers", [])
    )
    instance = capacity.get("instance_type") or {}
    footnote = (
        f"<p class='sub'>Machine type {_esc(instance.get('name'))} for "
        f"{_esc(instance.get('role'))}, quoted from {_esc(instance.get('citation'))}. "
        "Nothing here derives a machine type.</p>"
        if instance
        else ""
    )
    return (
        "<h2>Capacity</h2>"
        f"<p>Sized from {_esc(capacity['calls_per_node'])} calls per node: "
        f"{_esc(capacity.get('reason') or '')}</p>"
        "<table><thead><tr><th>Target concurrency</th><th>Nodes for load</th>"
        "<th>Spare</th><th>Total nodes</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>{footnote}"
    )


def load_report_filename(run_id: Any) -> str:
    """A filename that cannot smuggle anything into a header. See
    :func:`report_filename`."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "", str(run_id))[:32] or "run"
    return f"voicegateway-loadtest-{safe}.html"


#: The stamp's own styling, appended to the shared sheet. Deliberately loud:
#: this is the element that stops a fixture-built file being mistaken for a
#: measurement. No url(), no font import, nothing to fetch.
_LOAD_EXCLUSION_CSS = (
    ".exclusions{border:2px solid #b45309;background:#fffbeb;padding:12px 16px;"
    "margin:16px 0;border-radius:6px}"
    ".exclusions h2{margin-top:0}"
)

_LOAD_REPORT_CSS = """
.stamp{border:4px solid #b00020;background:#fff3f3;color:#7a0016;
padding:16px 20px;margin:0 0 24px;border-radius:6px}
.stamp strong{display:block;font-size:20px;letter-spacing:.08em}
.stamp p{margin:8px 0 0;color:#7a0016}
"""


def _render_scope_exclusions(payload: dict[str, Any]) -> str:
    """Parts of the criterion this system cannot evaluate, stated once and high.

    Directly under the verdict, because the verdict is exactly what they
    qualify. These were eighteen UNKNOWN gate rows on a real run, and collapsing
    them to one statement each is only honest while the statement is at least as
    hard to miss as the rows were.
    """
    exclusions = payload.get("scope_exclusions") or {}
    if not exclusions:
        return ""
    items = "".join(
        f"<li><strong>{_esc(resource.replace('_', ' '))}</strong>: {_esc(reason)}</li>"
        for resource, reason in sorted(exclusions.items())
    )
    return (
        '<div class="exclusions"><h2>Not in scope for this report</h2>'
        "<p>The acceptance criterion asks for headroom on network, RTP ports "
        "and system limits. Everything listed below is outside what this system "
        "can measure at all, so the requirement is <strong>not fully "
        "covered</strong> whatever the verdict above says. These are not gaps "
        "in what this run collected: they are quantities nobody publishes a "
        "denominator for.</p>"
        f"<ul>{items}</ul></div>"
    )


def _render_run_identity(payload: dict[str, Any]) -> str:
    """WHEN the run happened and WHICH artifacts it was built from.

    A capacity report that does not say when the test ran is not filing-quality
    evidence: it cannot be matched to a change window, an incident, or the
    invoice for the hours it measured. The window lived in the payload and
    nowhere in the document, which showed only when the EXPORT was produced, a
    date that can be months later and says nothing about the test.

    The checksum is here for the same reason. It is what ties this document to
    the bytes it describes, and a reader holding two reports of two runs needs
    to be able to tell them apart without opening the JSON.

    Absent values are named as absent rather than omitted, because a header that
    silently drops the window reads as a report that never had one.
    """
    run = payload.get("run") or {}
    started = _utc(run.get("started_at_ms"))
    ended = _utc(run.get("ended_at_ms"))
    if started and ended:
        window = f"{_esc(started)} to {_esc(ended)}"
    elif started:
        window = f"{_esc(started)}, end not recorded"
    elif ended:
        window = f"start not recorded, ended {_esc(ended)}"
    else:
        window = '<span class="nm">not recorded</span>'
    checksum = run.get("artifact_sha256")
    digest = (
        f'<span class="mono">{_esc(str(checksum)[:16])}</span>'
        if checksum
        else '<span class="nm">no artifact checksum</span>'
    )
    return (
        '<p class="sub">Run window (UTC) '
        f"<strong>{window}</strong> &middot; artifacts {digest}</p>"
    )


def render_load_html(payload: dict[str, Any]) -> str:
    """Render a load-test payload as ONE self-contained HTML document.

    SYNCHRONOUS. Same hard rule as :func:`render_html`: no script, no link, no
    image, no external font, no url() and no absolute URL anywhere, because this
    file is opened from disk, possibly offline, possibly years later.

    When provenance is not ``measured`` the synthetic stamp is the FIRST element
    in the body. A reader who scrolls straight to the numbers passes it on the
    way, which is the only placement that makes it hard to miss. Nothing is
    inserted above it, including the results reordering below.

    Order is the answer, then the working: verdict, per-test results, capacity,
    then the gate detail that produced them.
    """
    run_id = _esc(payload["run"]["id"])
    body = "".join(
        [
            # The stamp stays the FIRST element in the body. Its whole design
            # is that a reader who scrolls to the numbers passes it on the way,
            # and a verdict above it would be the first thing read on a file
            # built from fixtures.
            _render_stamp(payload),
            "<h1>Load-test run report</h1>",
            f'<p class="sub">VoiceGateway &middot; run '
            f'<span class="mono">{run_id}</span> &middot; provenance '
            f"<strong>{_esc(payload['data_provenance'])}</strong></p>",
            _render_run_identity(payload),
            # Above the gate table it summarises, and below the stamp.
            _render_verdict(payload),
            # Immediately under the verdict, because that is what they qualify.
            _render_scope_exclusions(payload),
            # THE ANSWER, THEN THE WORKING. The per-test table is the contracted
            # deliverable: concurrency, duration, establishment, peak CPU and
            # memory, failures by cause. It sat BELOW the gate detail, and on a
            # real run that meant scrolling past 48 gate rows to reach the three
            # rows somebody asked for. Capacity follows it because it is derived
            # from it; the gate detail is the working and now comes after both.
            _render_load_tests(payload),
            _render_capacity(payload),
            _render_gates(payload),
            _render_appendix(payload),
            _render_limits(payload),
            "<footer>Generated by voicegateway "
            f"{_esc(payload['generator']['version'])} on "
            f"{_esc(payload['generated_at'])} &middot; report schema v"
            f"{payload['schema_version']} ({_esc(payload['kind'])}). This file is "
            "self-contained: it loads nothing over the network and reads the same "
            "offline.</footer>",
        ]
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Load-test report {run_id}</title>"
        f"<style>{_REPORT_CSS}{_LOAD_REPORT_CSS}"
        f"{_LOAD_EXCLUSION_CSS}</style></head>"
        f"<body>{body}</body></html>\n"
    )


# ---------------------------------------------------------------------------
# Reproducible-test-assets appendix
# ---------------------------------------------------------------------------
#
# A capacity number is only evidence if somebody else can produce it again. The
# appendix carries what it takes to re-run the thing: the commands, the flag
# semantics they depend on, and the toolchain notes that make the binary build.
#
# Every entry REQUIRES a citation. Nothing here is derived, and an uncited
# command is indistinguishable from one this code invented, which is the same
# rule InstanceType follows for machine types.
#
# What this deliberately does NOT carry: the generator's scenario files, its
# configuration, or any part of its source. Flag names and their meanings are
# interface facts about a public tool. A scenario file is authored expression
# and belongs to whoever wrote it, so it is referenced BY NAME and never copied
# into this repository.

#: Anything matching this is reduced to its host before it reaches the HTML.
#: Two reasons, and either alone would be enough: an absolute URL breaks the
#: self-containment scan, and a report handed to somebody outside the
#: deployment should not carry its endpoints.
_URL_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://([^\s/\"'<>]+)(?:/\S*)?")

#: sip:, sips:, tel: and mailto: carry a host with no "//" at all, and a SIP
#: load test is exactly where those appear. Left alone they put a real endpoint
#: into a file that gets handed to somebody outside the deployment.
_OPAQUE_URI_RE = re.compile(r"\b(?:sips?|tel|mailto|data):([^\s\"'<>]+)", re.IGNORECASE)

#: Protocol-relative //host/path. It has no scheme to strip and would survive
#: the pattern above.
_SCHEMELESS_RE = re.compile(r"(?<![a-zA-Z0-9:/])//([^\s/\"'<>]+)(?:/\S*)?")


def redact_urls(text: str) -> str:
    """Reduce every absolute URL in ``text`` to a bare host label.

    ``wss://media.example.com/rtc`` becomes ``media.example.com``. The host is
    kept because a reader reproducing a run needs to know WHICH host, and the
    scheme and path are dropped because neither survives a self-containment
    scan and neither helps.
    """
    reduced = _URL_RE.sub(lambda m: m.group(1), text)
    reduced = _OPAQUE_URI_RE.sub(lambda m: m.group(1), reduced)
    return _SCHEMELESS_RE.sub(lambda m: m.group(1), reduced)


def appendix_entry(*, label: str, detail: str, citation: str) -> dict[str, str]:
    """One cited fact for the appendix.

    ``citation`` is required for the same reason a machine type needs one: this
    module cannot derive a command line, so an uncited one would be an invented
    instruction presented as a record of what was run.
    """
    if not citation.strip():
        raise ValueError(
            f"appendix entry {label!r} needs a citation: an uncited command is "
            "indistinguishable from one this report invented"
        )
    return {
        "label": label,
        "detail": redact_urls(detail),
        "citation": citation,
    }


def _render_appendix(payload: dict[str, Any]) -> str:
    """The reproducible-assets appendix, or a statement that there is none."""
    appendix = payload.get("appendix") or {}
    sections = []
    for key, heading, blurb in (
        (
            "commands",
            "Commands",
            "As run. Absolute URLs are reduced to a host label, so a command "
            "here may need its endpoint restored before it will execute.",
        ),
        (
            "flags",
            "Flag semantics",
            "Verify these against the binary you are holding. A generator's own "
            "documentation can disagree with its behaviour, and a flag whose "
            "default is wrong produces a run that completes and measures the "
            "wrong thing.",
        ),
        ("toolchain", "Toolchain", "What it takes to get a working binary."),
    ):
        entries = appendix.get(key) or []
        if not entries:
            continue
        rows = "".join(
            "<tr>"
            f"<td class='mono'>{_esc(item['label'])}</td>"
            f"<td>{_esc(item['detail'])}</td>"
            f"<td class='sub'>{_esc(item['citation'])}</td>"
            "</tr>"
            for item in entries
        )
        sections.append(
            f"<h3>{_esc(heading)}</h3><p class='sub'>{_esc(blurb)}</p>"
            "<table><thead><tr><th>Item</th><th>Detail</th><th>Source</th>"
            f"</tr></thead><tbody>{rows}</tbody></table>"
        )

    if not sections:
        return (
            "<h2>Reproducible test assets</h2>"
            "<p>None recorded. Without the commands and flag semantics behind "
            "them, the numbers above cannot be reproduced by anybody else, "
            "which is most of what makes them evidence.</p>"
        )
    return (
        "<h2>Reproducible test assets</h2>"
        "<p>What it takes to run this again. Scenario files and generator "
        "configuration are referenced by name rather than reproduced here: they "
        "are the work of whoever authored them.</p>" + "".join(sections)
    )


# ---------------------------------------------------------------------------
# Profile view: what was measured, and the range each number is read against
# ---------------------------------------------------------------------------
#
# A second RENDERING of the load payload, not a second payload. It takes exactly
# what :func:`build_load_payload` produces, so the two views cannot disagree
# about a number: there is one set of figures and two ways of presenting them.
#
# The difference is what each view is FOR. The acceptance view exists because a
# client engagement contracted thresholds, so it grades every gate and prints a
# verdict. This one exists because VoiceGateway is a profiler: its default
# output shows what it measured and the range each number is read against, and
# then stops. No verdict, no status word, no exit code. A human reads it and
# decides, because the person accountable for the deployment is the one holding
# the context a threshold cannot carry.
#
# That is why the status field is never rendered here, not even as a colour. A
# report that grades has to be right about the threshold; a report that measures
# only has to be right about the measurement, and the second claim is the one
# this system can actually stand behind.

#: What ``kind`` a consumer matches on to tell this VIEW from the acceptance
#: view of the same run. The payload's own ``kind`` is unchanged and still says
#: ``voicegateway.loadtest.run_report``: this is a rendering of that payload,
#: not a new schema, and the footer prints both so a reader holding the HTML can
#: tell which document they have.
PROFILE_KIND = "voicegateway.loadtest.profile_report"

#: Resources that can never be measured by anybody, so their gate rows are
#: DROPPED from this document entirely rather than listed as not collected.
#:
#: A "not collected" row is a promise that somebody could collect it. Nobody
#: publishes a per-instance-type packets-per-second allowance, under any API or
#: at any price, so a row saying "unknown" for PPS headroom is an item on a
#: checklist that can never be ticked. It is noise in a document whose whole job
#: is to separate what was measured from what was not.
#:
#: THE SOURCE OF TRUTH IS :data:`voicegateway.loadtest.judge.
#: PERMANENT_HEADROOM_EXCLUSIONS`, whose keys are exactly these names and whose
#: reasoning lives on :data:`gates.HEADROOM_PPS`. It is named here from the
#: gates constant rather than imported from judge for two reasons, either of
#: which alone would be enough. This module's docstring promises it touches no
#: storage, and importing judge pulls in the repository layer through
#: ``loadtest.aggregation``. And it would make ``livekit_diag`` depend on
#: ``loadtest``, which already depends on ``livekit_diag.gates``, so the first
#: loadtest module to import a renderer closes the loop. Naming the shared
#: ``gates`` constant keeps a rename from silently breaking the match, and a
#: test can assert this set equals judge's keys without either module importing
#: the other.
PROFILE_PERMANENTLY_UNMEASURABLE: frozenset[str] = frozenset({gates.HEADROOM_PPS})

#: Where each measurement is filed, grouped by WHAT THE NUMBER TEACHES rather
#: than by which gate family produced it. A reader asking "did the boxes have
#: room" does not care that file descriptors arrive through the headroom gate
#: and CPU through its own; they care that both describe one node's limits.
#:
#: Keys are profile keys, not gate ids. Headroom is one gate id covering several
#: resources that belong in different groups (file descriptors are a node limit,
#: RTP ports are a media limit), so a headroom row is keyed
#: ``resource_headroom/<resource>``. Everything else is keyed by gate id.
#:
#: Declared as a mapping so a new gate has to be CLASSIFIED rather than falling
#: through into whichever branch a renderer happens to reach. Anything unknown
#: renders under :data:`PROFILE_UNGROUPED_GROUP` where somebody will see it,
#: which is the point: a measurement that quietly vanishes from a profiler is
#: worse than one filed under the wrong heading.
PROFILE_GROUPS: dict[str, tuple[str, ...]] = {
    "Node resources": (
        gates.NODE_CPU_GATE,
        gates.NODE_MEMORY_GATE,
        f"{gates.HEADROOM_GATE}/{gates.HEADROOM_FILE_DESCRIPTORS}",
    ),
    "Media and network": (
        f"{gates.HEADROOM_GATE}/{gates.HEADROOM_RTP_PORTS}",
        f"{gates.HEADROOM_GATE}/{gates.HEADROOM_NETWORK_IN}",
        f"{gates.HEADROOM_GATE}/{gates.HEADROOM_NETWORK_OUT}",
        gates.NETWORK_ALLOWANCE_GATE,
    ),
    "Stability over time": (
        gates.PROCESS_LIFECYCLE_GATE,
        gates.RESOURCE_TREND_GATE,
        gates.RETURN_TO_BASELINE_GATE,
    ),
    "Call handling": (
        gates.ESTABLISHMENT_GATE,
        gates.SUSTAINED_HEALTH_GATE,
    ),
}

#: Where an unclassified measurement lands. Deliberately visible and
#: deliberately unflattering to read: it is a prompt to classify the gate, not a
#: permanent home.
PROFILE_UNGROUPED_GROUP = "Not yet classified"

_CLASSIFIED_GATE_IDS: frozenset[str] = frozenset(
    key.split("/", 1)[0] for keys in PROFILE_GROUPS.values() for key in keys
)

#: Gate ids :data:`PROFILE_GROUPS` does not place, computed rather than written
#: down so it cannot drift from the mapping above.
#:
#: Today it is the four DIAGNOSTICS gates: agents, reply latency and the two SFU
#: gates. A load payload never carries them, because a load run is an external
#: generator placing calls and not this host probing an SFU, so leaving them
#: unclassified is the accurate statement rather than an omission. They are
#: named here so a test can pin the list and a fifth entry appearing is a
#: question somebody has to answer.
PROFILE_UNCLASSIFIED_GATES: frozenset[str] = frozenset(
    gates.ALL_GATES - _CLASSIFIED_GATE_IDS
)

#: One human name and one "what it means" sentence per measurement, keyed the
#: same way as :data:`PROFILE_GROUPS`.
#:
#: The name is what the number IS, in the words somebody reading a capacity
#: report already uses. The sentence is what it teaches, and it carries the
#: caveat that changes how the figure should be read: which denominator it is a
#: fraction of, whether that denominator was measured or declared, and whether
#: it is a fraction at all.
_PROFILE_MEASUREMENTS: dict[str, tuple[str, str]] = {
    gates.NODE_CPU_GATE: (
        "Peak node CPU",
        "How close the busiest node came to using all of its CPU while the test "
        "ran. The worst node over the window, not a fleet average.",
    ),
    gates.NODE_MEMORY_GATE: (
        "Peak node memory",
        "How close the busiest node came to using all of its memory while the "
        "test ran. The worst node over the window, not a fleet average.",
    ),
    f"{gates.HEADROOM_GATE}/{gates.HEADROOM_FILE_DESCRIPTORS}": (
        "File-descriptor headroom",
        "The share of the process file-descriptor limit that stayed unused. "
        "Both halves are real counters, so this fraction is measured end to end.",
    ),
    f"{gates.HEADROOM_GATE}/{gates.HEADROOM_RTP_PORTS}": (
        "RTP media-port headroom",
        "The share of the media port range that stayed free. The range size is "
        "declared configuration rather than a measurement, so a node that "
        "publishes occupancy without it reports nothing rather than a guess.",
    ),
    f"{gates.HEADROOM_GATE}/{gates.HEADROOM_NETWORK_IN}": (
        "Inbound network headroom",
        "Observed inbound throughput against the instance type's DECLARED "
        "baseline. The instance can burst above that baseline, so this reads "
        "high rather than low.",
    ),
    f"{gates.HEADROOM_GATE}/{gates.HEADROOM_NETWORK_OUT}": (
        "Outbound network headroom",
        "Observed outbound throughput against the instance type's DECLARED "
        "baseline. Judged apart from inbound because the hypervisor meters the "
        "two against separate credit buckets.",
    ),
    gates.HEADROOM_GATE: (
        "Resource headroom",
        "Headroom on a limited resource, with no resource named on the row: "
        "read the recorded detail beside it to find out which one.",
    ),
    gates.NETWORK_ALLOWANCE_GATE: (
        "Hypervisor allowance events",
        "How many times the hypervisor recorded an allowance as exceeded during "
        "the window. A count of throttling EVENTS, not a fraction of a limit: "
        "the limit itself is unpublished.",
    ),
    gates.PROCESS_LIFECYCLE_GATE: (
        "Restarts and OOM kills",
        "Processes that restarted, and kernel OOM kills, across the window. "
        "Either one means something died and came back while the test ran.",
    ),
    gates.RESOURCE_TREND_GATE: (
        "Resource drift",
        "How far a resource moved between the middle and the last third of the "
        "steady-state window, in that resource's own units.",
    ),
    gates.RETURN_TO_BASELINE_GATE: (
        "Return to baseline",
        "Where a resource settled after teardown, as a multiple of its own idle "
        "baseline from before the run. 1.0 is exactly back to where it started.",
    ),
    gates.ESTABLISHMENT_GATE: (
        "Calls established",
        "The share of call attempts that reached an established call, counted "
        "from the generator's own records over one network path.",
    ),
    gates.SUSTAINED_HEALTH_GATE: (
        "Consecutive failed health samples",
        "The longest unbroken run of failed dependency or health-check samples. "
        "A count of samples, so consecutive rather than cumulative.",
    ),
}

#: How a not-collected row is filed, and what to do about it. Ordered: the first
#: marker that appears in the gate's recorded detail wins.
#:
#: Classified from the DETAIL because that is the only cause the payload
#: carries. The gate records why it could not evaluate in prose and nowhere as a
#: code, so this matches on the phrases those gates actually write. It is
#: fragile by construction, which is why the fallback below is a named cause
#: rather than a silent bucket, and why every row prints its own detail verbatim
#: underneath: a misfiled row is visible to the reader, not hidden by the
#: heading it landed under.
#:
#: Grouped by cause rather than by gate so the section reads as a setup
#: checklist. Twelve rows saying "not measured" are one action when they share a
#: cause, and a reader who has to derive that themselves does not.
_PROFILE_CAUSES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        (
            "no scrape target",
            "nothing in the scrape set publishes",
            "outside scope for this report",
        ),
        "Nothing was configured to answer it",
        "No source in the scrape set can produce this at all, so it was never "
        "asked. Wire the exporter or endpoint the detail names and run again. "
        "Until then the measurement does not exist, which is not the same as it "
        "coming back clean.",
    ),
    (
        ("usable sample",),
        "The window carried too few samples",
        "A trend is computed over thirds of the steady-state window and each "
        f"third needs at least {gates.MIN_TREND_SAMPLES} usable samples. Run for "
        "longer, or sample more often. Too short to tell is not flat.",
    ),
    (
        (),
        "The series was not in this run's scrape",
        "These are collectable and simply were not in the scrape correlated to "
        "this window: the exporter or the series was wired after the run was "
        "captured, or nothing was scraped for the window at all. Re-run against "
        "the current scrape configuration and they fill in.",
    ),
)


def _headroom_resource(gate: dict[str, Any]) -> str | None:
    """Which resource a headroom gate is about, from whichever field survived.

    ``metric`` is ``<resource>_headroom`` and is the reliable source, but it is
    None on every gate that could not evaluate, which is most of the rows this
    view has to file. The subject is built as ``<node>[/<source>]/<resource>``,
    so the last segment is the resource on both the measured and the unmeasured
    row.

    Nothing is validated against a list of known resources on purpose. A
    resource this build has never heard of must reach
    :data:`PROFILE_UNGROUPED_GROUP` and be seen, and a whitelist would instead
    fold it back into the bare headroom key where it looks classified.
    """
    metric = gate.get("metric")
    if isinstance(metric, str) and metric.endswith("_headroom"):
        return metric[: -len("_headroom")] or None
    subject = gate.get("subject")
    if isinstance(subject, str) and "/" in subject:
        return subject.rsplit("/", 1)[1] or None
    return None


def _profile_key(gate: dict[str, Any]) -> str:
    """The key a gate is grouped and named by. See :data:`PROFILE_GROUPS`."""
    gate_id = str(gate.get("gate") or "")
    if gate_id != gates.HEADROOM_GATE:
        return gate_id
    resource = _headroom_resource(gate)
    return f"{gate_id}/{resource}" if resource else gate_id


def _profile_name(key: str) -> tuple[str, str]:
    """The human name and the "what it means" sentence for one key.

    A key with no entry falls back to the key itself rather than to a blank, and
    says out loud that this build does not know what it is. An unnamed row is
    still a measurement somebody took, and dropping it because the renderer has
    no label for it would be the renderer deciding what counts.
    """
    named = _PROFILE_MEASUREMENTS.get(key)
    if named is not None:
        return named
    return (
        key.replace("_", " ").replace("/", ": "),
        "This build carries no description for this measurement, so read the "
        "recorded detail beside it rather than inferring what it means.",
    )


def _profile_cause(detail: str) -> tuple[str, str]:
    """Why nothing was recorded, and what closes the gap. See
    :data:`_PROFILE_CAUSES`."""
    lowered = detail.lower()
    for markers, title, remedy in _PROFILE_CAUSES:
        if any(marker in lowered for marker in markers):
            return title, remedy
    return _PROFILE_CAUSES[-1][1], _PROFILE_CAUSES[-1][2]


def _profile_rows(
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Split the payload's gates into measured, not-collected and dropped rows.

    Order is the payload's own order throughout, which is the order the run
    executed in. Re-sorting would break the correspondence with the per-test
    table above it, and a reader matching a row to a step should not have to
    hold two orderings in their head.

    Permanently unmeasurable resources are dropped here rather than filtered at
    render time, so no downstream count includes a row nobody will ever see.
    """
    measured: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for gate in payload.get("gates") or []:
        if not isinstance(gate, dict):
            continue
        key = _profile_key(gate)
        name, meaning = _profile_name(key)
        detail = str(gate.get("detail") or "")
        row = {
            "key": key,
            "gate": str(gate.get("gate") or ""),
            "name": name,
            "meaning": meaning,
            "subject": gate.get("subject"),
            "step": gate.get("step"),
            "value": _as_float(gate.get("value")),
            "threshold": _as_float(gate.get("threshold")),
            "detail": detail,
        }
        resource = (
            _headroom_resource(gate) if row["gate"] == gates.HEADROOM_GATE else None
        )
        if resource in PROFILE_PERMANENTLY_UNMEASURABLE:
            dropped.append(row)
        elif row["value"] is None:
            missing.append(row)
        else:
            measured.append(row)
    return measured, missing, dropped


def _profile_subject(row: dict[str, Any]) -> str:
    """Which node, source and step a row belongs to.

    The step is part of a row's identity and not decoration, for the same reason
    it is on the gate table: a seven-step run otherwise renders seven rows with
    identical subjects and nothing to tell them apart. Suppressed where it
    merely repeats the subject, which is the establishment row, whose subject IS
    the step.
    """
    subject, step = row.get("subject"), row.get("step")
    cell = _recorded(subject)
    if step and step != subject:
        cell += f'<div class="why">{_esc(step)}</div>'
    return cell


def _profile_band(value: float, threshold: float) -> str:
    """The reference band: a track, a neutral fill, and a tick at the reference.

    THE FILL IS ONE NEUTRAL COLOUR FOR ITS WHOLE LENGTH, on both sides of the
    tick, and that is the single most important line in this function. Colouring
    a bar by which side of a line it lands on is a verdict wearing a chart's
    clothes: it grades the number before the reader has read it, in the one view
    that exists to stop doing exactly that. The tick says where the reference
    sits; whether landing past it is fine is the reader's call, and this
    document does not have the context to make it for them.

    CSS only. No SVG and no image: three test suites scan the output for those
    markers, because this file has to render from disk with no network, and a
    chart library is what somebody reaches for the first time a bar is wanted.
    """
    fill = max(0.0, min(100.0, value * 100.0))
    tick = max(0.0, min(100.0, threshold * 100.0))
    return (
        '<div class="band">'
        f'<div class="band-fill" style="width:{_esc(f"{fill:.1f}")}%"></div>'
        f'<div class="band-tick" style="left:{_esc(f"{tick:.1f}")}%"></div>'
        "</div>"
    )


def _profile_reference(row: dict[str, Any]) -> str:
    """The range a value is read against: a band where one exists, else words.

    A band is drawn ONLY for a gate in :data:`gates.RATIO_GATES` whose value and
    reference both fall inside 0..1. There the full scale is exactly 1.0, and
    1.0 is a real thing: the limit the fraction was taken against. The bar's
    length then means what a reader assumes it means.

    Everything else gets the figure and no bar, and this is the decision the
    alternative was worse than. Scaling a count against
    ``max(value, threshold) * 1.25`` would put a fill on the page whose
    denominator this renderer invented, so its width would mean "some share of a
    number nobody measured". A reader reads a bar as a fraction of something
    real. Restarts, OOM kills, allowance events and consecutive failed samples
    have no maximum, and neither does a return-to-baseline ratio, which is a
    multiple of a baseline and runs past 1 by design. So no bar is drawn for any
    of them. It is the same reasoning that drops the PPS rows entirely: where
    there is no denominator, the honest rendering is the numerator alone.
    """
    value, threshold = row["value"], row["threshold"]
    if threshold is None:
        return (
            '<span class="nm">no reference recorded: this run carried no value '
            "to read the figure against</span>"
        )
    reference = _gate_number(threshold, row["gate"])
    if (
        row["gate"] in gates.RATIO_GATES
        and value is not None
        and 0.0 <= value <= 1.0
        and 0.0 <= threshold <= 1.0
    ):
        return (
            _profile_band(value, threshold) + '<div class="band-scale"><span>0%</span>'
            f"<span>reference {reference}</span><span>100%</span></div>"
        )
    return (
        f"reference {reference}"
        '<div class="why">No bar: this figure is a count, or a multiple of a '
        "baseline, so there is no full scale to draw it against.</div>"
    )


def _render_profile_measurements(measured: list[dict[str, Any]]) -> str:
    """Everything this run put a number on, grouped by what the number teaches."""
    if not measured:
        return (
            "<h2>Measurements</h2>"
            '<div class="note">This run put a number on nothing. Every gate it '
            "evaluated recorded an absence, and those are listed below with the "
            "reason each one gives. Read that section as the whole result: there "
            "is no measured half hiding behind it.</div>"
        )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in measured:
        title = next(
            (t for t, keys in PROFILE_GROUPS.items() if row["key"] in keys),
            PROFILE_UNGROUPED_GROUP,
        )
        grouped.setdefault(title, []).append(row)
    out = [
        "<h2>Measurements</h2>"
        "<p>Every figure this run recorded, with the range it is read against. "
        "The reference is the value the number is compared to; nothing here says "
        "whether landing on either side of it is acceptable, because that "
        "depends on what the deployment is for.</p>"
    ]
    # PROFILE_GROUPS order first, then anything unclassified, so a new gate
    # surfaces at the bottom of the section instead of shuffling the groups a
    # reader knows.
    order = [t for t in PROFILE_GROUPS if t in grouped]
    order += [t for t in grouped if t not in PROFILE_GROUPS]
    for title in order:
        body = "".join(
            f"<tr><td class='mono'>{_profile_subject(row)}</td>"
            f"<td>{_esc(row['name'])}"
            f"<div class='why'>{_esc(row['meaning'])}</div></td>"
            f"<td class='num'>{_gate_number(row['value'], row['gate'])}</td>"
            f"<td>{_profile_reference(row)}</td></tr>"
            for row in grouped[title]
        )
        out.append(
            f"<h3>{_esc(title)}</h3>"
            "<table><thead><tr><th>Subject</th><th>Measurement</th>"
            "<th class='num'>Value</th><th>Read against</th></tr></thead>"
            f"<tbody>{body}</tbody></table>"
        )
    if PROFILE_UNGROUPED_GROUP in grouped:
        out.append(
            '<div class="note">The measurements above are real and this build '
            "does not know where to file them. They are shown rather than "
            "dropped: a profiler that silently loses a number it took is worse "
            "than one that admits it has no heading for it.</div>"
        )
    return "".join(out)


def _render_profile_not_collected(
    payload: dict[str, Any],
    missing: list[dict[str, Any]],
    dropped: list[dict[str, Any]],
) -> str:
    """The setup checklist: what produced no number, grouped by why.

    Rows for permanently unmeasurable resources are absent from this list on
    purpose, and a single sentence names them when any were dropped. The list
    itself is a queue of work somebody can do, and an item nobody can ever do
    turns it into a queue that never empties. Naming them once keeps the
    disclosure from shrinking silently, which is the failure mode of collapsing
    rows into a summary.
    """
    run_limits = payload.get("run_limitations") or []
    out = ["<h2>Not collected in this run</h2>"]
    if not missing and not run_limits:
        out.append(
            "<p>Every gate this run evaluated produced a number, and every "
            "number is in the section above.</p>"
        )
    else:
        out.append(
            "<p>These produced no number. They are grouped by cause so the "
            "section reads as a list of things to fix rather than as a list of "
            "gaps, and each row carries what the run itself recorded, verbatim. "
            "An absence is never a zero and never a pass: nothing was compared "
            "against anything.</p>"
        )
    if dropped:
        names = sorted(
            {str(r["subject"] or r["key"]).rsplit("/", 1)[-1] for r in dropped}
        )
        out.append(
            f"<p class='sub'>{len(dropped)} row(s) covering "
            f"{_esc(', '.join(names))} are absent from this list entirely, not "
            "filed as uncollected: no allowance for them is published by "
            "anybody, so there is no denominator to divide by and no run will "
            "ever fill them in.</p>"
        )
    grouped: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for row in missing:
        title, remedy = _profile_cause(row["detail"])
        grouped.setdefault(title, (remedy, []))[1].append(row)
    for _markers, title, _remedy in _PROFILE_CAUSES:
        entry = grouped.get(title)
        if entry is None:
            continue
        remedy, rows = entry
        body = "".join(
            f"<tr><td class='mono'>{_profile_subject(row)}</td>"
            f"<td>{_esc(row['name'])}</td>"
            f"<td>{_esc(row['detail'])}</td></tr>"
            for row in rows
        )
        out.append(
            f"<h3>{_esc(title)}</h3><p class='sub'>{_esc(remedy)}</p>"
            "<table><thead><tr><th>Subject</th><th>Measurement</th>"
            "<th>What the run recorded</th></tr></thead>"
            f"<tbody>{body}</tbody></table>"
        )
    if run_limits:
        entries = "".join(f"<li>{_esc(item)}</li>" for item in run_limits)
        out.append(
            "<h3>From this run's own artifacts</h3>"
            "<p class='sub'>Gaps in the files this report was built from, rather "
            "than in the fleet it describes. A later run can close them.</p>"
            f"<ul>{entries}</ul>"
        )
    return "".join(out)


def _render_profile_method(payload: dict[str, Any]) -> str:
    """How to read the figures above, including the reasons they read low or high.

    The structural limits are :data:`_LOAD_REPORT_LIMITS`, reused through the
    payload rather than restated: a second copy of that list is a second list
    that goes stale, and the version a client reads would be the stale one. What
    is added here is the reading rules a profile view needs and a gated view
    does not, because a gated view hands the reader a verdict and this one hands
    them the numbers.
    """
    structural = "".join(f"<li>{_esc(item)}</li>" for item in payload["not_measured"])
    method = [
        "There is no verdict in this document, and that is deliberate. It "
        "reports what was measured and the range each number is read against, "
        "then stops. Whether a figure is acceptable depends on what the "
        "deployment is for, and the person accountable for it holds context "
        "this file cannot carry.",
        "A reference bar is ONE neutral colour for its whole length, on both "
        "sides of the tick. It is never coloured by which side the value lands "
        "on, because a bar that changes colour at a line has graded the number "
        "before the reader read it.",
        "A bar is drawn only where a real full scale exists: a fraction of a "
        "limit, where 100% is the limit itself. A count has no maximum, so a "
        "count is shown as a figure with its reference beside it and no bar.",
        "A gap is NULL, and renders as absent. Nothing unmeasured is shown as "
        "0, because zero and unmeasured support opposite conclusions and the "
        "difference cannot be recovered once it is lost.",
        f"Below {gates.MIN_PERCENTILE_SAMPLES} samples a peak is the max of N "
        "rather than a percentile. A percentile computed from a handful of "
        "samples is the maximum wearing a more confident label.",
    ]
    items = "".join(f"<li>{_esc(item)}</li>" for item in method)
    return (
        "<h2>How to read these numbers</h2>"
        f"<ul>{items}</ul>"
        "<h3>What this report structurally does not measure</h3>"
        "<p>These are limits of the system, not of this run. A later run does "
        "not close them.</p>"
        f"<ul>{structural}</ul>"
    )


def _render_profile_identity(payload: dict[str, Any]) -> str:
    """Which run this is, as a plain definition list.

    No bordered box. The acceptance view boxes its metadata because it sits
    beside a verdict panel and needs to hold its own against it; here the
    metadata is the second thing on the page and a border around it only says
    "this part is separate", which is not true.

    Provenance is the FIRST row, ahead of the identifiers. A reader who stops
    after one line should have read whether these numbers describe anything
    real.
    """
    run = payload.get("run") or {}
    started, ended = _utc(run.get("started_at_ms")), _utc(run.get("ended_at_ms"))
    if started and ended:
        window = f"{_esc(started)} to {_esc(ended)}"
    elif started:
        window = f"{_esc(started)}, end not recorded"
    elif ended:
        window = f"start not recorded, ended {_esc(ended)}"
    else:
        window = '<span class="nm">not recorded</span>'
    checksum = run.get("artifact_sha256")
    rows = [
        (
            "Data provenance",
            f"{_esc(payload.get('data_provenance'))}"
            f'<div class="why">{_esc(payload.get("provenance_basis") or "")}</div>',
        ),
        ("Run id", f'<span class="mono">{_esc(run.get("id"))}</span>'),
        ("Label", _recorded(run.get("label"))),
        ("Project", _recorded(run.get("project"))),
        ("Run window (UTC)", window),
        (
            "Artifact checksum",
            f'<span class="mono">{_esc(str(checksum)[:16])}</span>'
            if checksum
            else '<span class="nm">no artifact checksum</span>',
        ),
        (
            "Generator",
            _recorded(run.get("tool"))
            + (f" {_esc(run.get('tool_version'))}" if run.get("tool_version") else ""),
        ),
        ("Report generated", _esc(payload["generated_at"])),
        (
            "Generated by",
            f"voicegateway {_esc(payload['generator']['version'])}"
            f" &middot; payload schema v{payload['schema_version']}",
        ),
    ]
    body = "".join(f"<dt>{label}</dt><dd>{value}</dd>" for label, value in rows)
    return f'<div class="ident"><dl>{body}</dl></div>'


#: Appended to :data:`_REPORT_CSS`, never merged into it. That sheet is shared
#: with the diagnostics and acceptance documents and several suites pin what it
#: renders, so a class added for this view belongs in its own constant where it
#: cannot change either of those.
#:
#: Carries the print rules the load sheet never had. A capacity report is
#: printed and filed, and without a repeating table head a page-two row is a
#: line of numbers with no column names above it, which is worse than useless
#: because it is still readable.
_PROFILE_CSS = """
.ident dl { display: grid; grid-template-columns: 200px 1fr; gap: 5px 18px;
            margin: 14px 0 6px; font-size: 13px; }
.ident dt { color: #5b6472; }
.ident dd { margin: 0; }
.why { color: #5b6472; font-size: 12px; margin: 2px 0 0; line-height: 1.4; }
.band { position: relative; height: 12px; min-width: 130px; margin: 4px 0 5px;
        background: #edeff3; border: 1px solid #dfe2e7; border-radius: 3px; }
/* ONE neutral fill, both sides of the tick. Colouring it by which side the
   value lands on would grade the number, which is the whole thing this view
   exists to remove. See _profile_band. */
.band-fill { position: absolute; top: 0; bottom: 0; left: 0; background: #9aa3b0;
             border-radius: 2px; }
.band-tick { position: absolute; top: -3px; bottom: -3px; width: 2px;
             margin-left: -1px; background: #16181d; }
.band-scale { display: flex; justify-content: space-between; gap: 10px;
              font-size: 11px; color: #5b6472; }
@page { margin: 16mm 14mm; }
@media print {
  thead { display: table-header-group; }
  tr { break-inside: avoid; }
  h2, h3 { break-after: avoid; }
}
"""


def profile_filename(run_id: Any) -> str:
    """A filename that cannot smuggle anything into a header. See
    :func:`report_filename`.

    A different stem from :func:`load_report_filename` on purpose. The two
    documents describe the same run and say different things about it, so they
    have to be able to sit in one directory without one overwriting the other,
    and somebody holding a file should be able to tell which view it is without
    opening it.
    """
    safe = re.sub(r"[^A-Za-z0-9_-]", "", str(run_id))[:32] or "run"
    return f"voicegateway-profile-{safe}.html"


def render_profile_html(payload: dict[str, Any]) -> str:
    """Render a load payload as the PROFILE view: measurements, no judgement.

    Takes exactly what :func:`build_load_payload` produces, which is the whole
    point of the signature. The acceptance view and this one read one payload,
    so they cannot disagree about a number: only about how much of it they show.

    What this deliberately does NOT render, in a document that has every input
    needed to render all three. There is no verdict block, no status word on any
    row, and no gate table. Those are the acceptance view, and it still exists
    for the engagement that contracted thresholds. Reproducing them here as
    well, in the profiler's default output, would make the threshold the headline
    of every run, including the many where nobody agreed to one.

    The measurement rows are derived FROM ``payload["gates"]`` rather than from
    the findings, because a gate row already carries the four things a profile
    row needs: what it is about, the number, the reference, and what the run
    recorded about it. Deriving them separately would be a second path to the
    same figures, and two paths is how the two views start to disagree.

    SYNCHRONOUS, and self-contained on the same hard terms as
    :func:`render_load_html`: no script, no link, no image, no SVG, no external
    font, no url() and no absolute URL anywhere in the output. This file is
    opened from disk, possibly offline, possibly years later.
    """
    measured, missing, dropped = _profile_rows(payload)
    run_id = _esc((payload.get("run") or {}).get("id"))
    body = "".join(
        [
            "<h1>Load-test profile</h1>",
            # Not in the section order this view was specified with, and kept
            # anyway: the stamp is the element that stops a fixture-built file
            # being mistaken for a measurement, and it renders nothing at all
            # when the run was measured. Dropping a disclosure because a layout
            # list did not mention it is how disclosures disappear.
            _render_stamp(payload),
            _render_profile_identity(payload),
            # Reused verbatim from the acceptance view rather than reimplemented.
            # These two sections carry no status and no verdict, so they are
            # already the profile rendering of those numbers, and a second
            # implementation would be a second set of figures to keep in step.
            _render_load_tests(payload),
            _render_profile_measurements(measured),
            _render_capacity(payload),
            _render_profile_not_collected(payload, missing, dropped),
            _render_profile_method(payload),
            "<footer>Generated by voicegateway "
            f"{_esc(payload['generator']['version'])} on "
            f"{_esc(payload['generated_at'])} &middot; "
            f"{_esc(PROFILE_KIND)}, rendered from payload schema v"
            f"{payload['schema_version']} ({_esc(payload.get('kind'))}). This is "
            "the profile view: it reports what was measured and stops. This file "
            "is self-contained: it loads nothing over the network and reads the "
            "same offline.</footer>",
        ]
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Load-test profile {run_id}</title>"
        f"<style>{_REPORT_CSS}{_LOAD_REPORT_CSS}{_PROFILE_CSS}</style></head>"
        f"<body>{body}</body></html>\n"
    )
