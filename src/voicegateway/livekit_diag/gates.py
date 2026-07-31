"""THE health gates for a LiveKit diagnostics run: one verdict, in one place.

Until this module existed there were **two** verdict implementations, and they
disagreed. ``service._verdict`` read the dashboard's per-check payloads;
``report.check_json`` read the CLI's raw probe objects. On every input where
they differed, the dashboard was the lenient one:

============================================  ==================  =================
input                                         ``_verdict`` said   ``check_json`` said
============================================  ==================  =================
a probe with zero successful trials           PASS                WARN
SFU baseline quality ``Poor`` / ``Lost``      WARN                FAIL
a check that errored or timed out             FAIL                (unreachable)
an ``sfu_load`` baseline                      never read at all   n/a
no latency result at all (no agent in a room) PASS                PASS
============================================  ==================  =================

They are collapsed here with **the stricter reading winning every time**, plus
the one rule neither of them had:

    **A gate that could not be evaluated does not pass.**

That last rule is what closes the bottom row of the table. A LiveKit server with
no agent in any room used to report a clean PASS from both implementations --
the latency loop simply had nothing to iterate, so nothing ever said the gate had
not run. A gate that under-reports is worse than no gate, because people trust
it, so "no samples" is now :data:`UNKNOWN` and exits non-zero.

**What is deliberately NOT gated.**

* ``loss_pct``. ``sfu.py`` hardcodes it to ``0.0`` (per-connection loss is not
  exposed by the SDK), so both old implementations carried a ``loss_pct > 1.0``
  branch that can never fire. Gating on a constant is theatre: the branch is gone
  and ``quality`` carries the connection signal that is real.
* the number of agents in rooms. LiveKit's server API does not report an idle
  registered worker, so "zero agents" is the normal reading of a healthy fleet
  between calls. A count gate would red-flag it.
* ``knee`` as a number. :func:`voicegateway.livekit_diag.sfu.find_knee` returns
  ``None`` for two opposite outcomes -- nothing breached, or the *first* tier
  breached -- so :func:`sfu_capacity_gate` reads the ramp steps directly and only
  answers the question that ambiguity hides: was even the smallest tier healthy?

**Percentile naming.** A gate never prints ``p95`` for a statistic it did not
compute. ``MAX_LATENCY_TRIALS`` is 3 and ``voicegw livekit check`` probes twice,
so in practice the slow-tail statistic is always the max of 2 or 3 samples, and
that is exactly what the metric name says (``..._max_of_2_ms``). A real p95 is
computed, and named ``p95``, only from :data:`MIN_PERCENTILE_SAMPLES` samples up,
through ``utils.percentiles.compute_percentiles`` (the standard for new
surfaces).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from voicegateway.utils.percentiles import compute_percentiles

PASS = "PASS"
WARN = "WARN"
UNKNOWN = "UNKNOWN"
FAIL = "FAIL"

# Severity, worst last. UNKNOWN outranks WARN on purpose: a WARN is bounded
# knowledge ("1.8s against your 1.5s target"), while a gate that could not
# evaluate is unbounded ("it might be 30s, or the agent might be dead"). The
# run's verdict is the worst gate, so a run that both measured a degradation and
# failed to measure something reports the un-measured half, which is the part
# nobody can act on until it is fixed.
_SEVERITY = {PASS: 0, WARN: 1, UNKNOWN: 2, FAIL: 3}

# Below this many samples a percentile is not a percentile. See the module
# docstring: with MAX_LATENCY_TRIALS = 3 this branch is the normal one, and the
# metric name says "max of N" instead of lying about a p95.
MIN_PERCENTILE_SAMPLES = 10

# LiveKit's ConnectionQuality values that mean the connection is not usable.
_DEGRADED_QUALITY = frozenset({"Poor", "Lost"})
# What SfuProbe._measure reports when no client connected: not a quality reading.
_NO_QUALITY = "Unknown"

# Stable gate ids. They are printed in CI logs and travel in the JSON payload, so
# they are part of the contract; add to them rather than renaming.
AGENTS_GATE = "agents_listing"
LATENCY_GATE = "agent_reply_latency"
SFU_QUALITY_GATE = "sfu_connection_quality"
SFU_CAPACITY_GATE = "sfu_capacity"


@dataclass(frozen=True)
class GateResult:
    """One gate's answer, with the number and threshold that produced it.

    ``metric`` names the statistic that actually decided the gate (never a
    percentile the gate did not compute), so a CI log line is self-explanatory:
    the reader does not have to guess whether ``1800`` was an average or a tail.
    It is ``None`` when the gate could not evaluate, because then no number
    decided anything.
    """

    gate: str
    status: str
    detail: str
    subject: str | None = None
    metric: str | None = None
    value: float | None = None
    threshold: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """A JSON-safe copy (the run payload is persisted and served as JSON)."""
        return asdict(self)


def worst_status(statuses: Iterable[str]) -> str:
    """The most severe status in ``statuses``; :data:`PASS` when empty.

    An unrecognised status is treated as :data:`FAIL`: a verdict vocabulary this
    module does not know is not something to optimistically round down.
    """
    out = PASS
    for status in statuses:
        rank = _SEVERITY.get(status, _SEVERITY[FAIL])
        if rank > _SEVERITY[out]:
            out = status if status in _SEVERITY else FAIL
    return out


def verdict(gates: Sequence[GateResult]) -> str:
    """The run verdict: the worst gate, or UNKNOWN when nothing was gated.

    No gates means no check produced anything a gate knows how to read. That is
    not a pass -- it is a run that evaluated nothing.
    """
    if not gates:
        return UNKNOWN
    return worst_status(g.status for g in gates)


def exit_code(run_verdict: str) -> int:
    """Process exit code for a verdict: 0 only for a clean PASS.

    One non-zero code on purpose. Splitting WARN / UNKNOWN / FAIL across 2 and 3
    would silently stop any existing ``if [ $? -eq 1 ]`` pipeline from noticing
    two thirds of the failures, which is the one way an exit-code change can make
    a gate weaker. WARN, UNKNOWN and FAIL all mean "this run did not demonstrate
    a healthy deployment", and all three exit 1.
    """
    return 0 if run_verdict == PASS else 1


# ---------------------------------------------------------------------------
# Individual gates
# ---------------------------------------------------------------------------


def agents_gate(result: dict[str, Any]) -> GateResult:
    """The agents check answered.

    This gate asserts only that LiveKit's server API responded with a list. It
    deliberately does NOT assert that any agent is online: ``list_agents``
    reports a worker only once it is IN a room, so an idle registered fleet
    returns an empty list, and a count gate would fail a perfectly healthy
    deployment between calls.
    """
    rows = result.get("agents") or []
    roster = result.get("roster")
    detail = f"{len(rows)} agent(s) in rooms"
    if roster is not None:
        detail += f"; {len(roster)} worker(s) on the heartbeat roster"
    return GateResult(gate=AGENTS_GATE, status=PASS, detail=detail)


def _has_measurement(stats: dict[str, Any]) -> bool:
    """Whether ``stats`` carries a real timing, as opposed to summarize's zeros.

    ``latency.summarize`` returns ``0.0`` for every statistic when there are no
    samples, and a real reply cannot arrive in zero seconds, so a 0.0 here means
    "nothing was measured" rather than "instant". This is the check that used to
    be missing: ``_verdict`` compared that fabricated 0.0 against the target,
    found it comfortably under, and called a probe that measured nothing a PASS.
    """
    for key in ("avg", "max"):
        try:
            if float(stats.get(key) or 0.0) > 0.0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _tail_statistic(
    stats: dict[str, Any], samples: Sequence[float]
) -> tuple[float | None, str]:
    """``(seconds, statistic_name)`` for the slow tail, named for what it is.

    A p95 is only computed (and only called ``p95``) from
    :data:`MIN_PERCENTILE_SAMPLES` samples up, via the shared
    ``compute_percentiles``. Below that the tail is the max, and the name says
    how many samples it is the max of.
    """
    if len(samples) >= MIN_PERCENTILE_SAMPLES:
        p95 = compute_percentiles(list(samples), [95.0]).get("p95")
        if p95 is not None:
            return float(p95), "p95"
    raw = stats.get("max")
    if raw is None:
        return None, "max"
    count = stats.get("trials")
    if count is None and samples:
        count = len(samples)
    name = f"max_of_{int(count)}" if count else "max"
    try:
        return float(raw), name
    except (TypeError, ValueError):
        return None, name


def latency_gates(
    entries: Sequence[dict[str, Any]], target_ms: float, *, strict: bool = False
) -> list[GateResult]:
    """One gate per probed agent; one UNKNOWN gate when nothing was probed.

    ``strict`` thresholds the slow tail instead of the average. An average under
    the target hides a tail well over it, and the tail is what a caller who hung
    up actually experienced -- but it is opt-in, because moving the default
    statistic would turn runs red for a reason unrelated to collapsing the two
    verdicts.
    """
    if not entries:
        return [
            GateResult(
                gate=LATENCY_GATE,
                status=UNKNOWN,
                detail=(
                    "no agent was probed, so reply latency was not measured "
                    "(LiveKit reports only agents already in a room)"
                ),
                threshold=target_ms,
            )
        ]
    return [_latency_gate(e, target_ms, strict=strict) for e in entries]


def _latency_gate(
    entry: dict[str, Any], target_ms: float, *, strict: bool
) -> GateResult:
    agent = str(entry.get("agent") or "?")
    stats: dict[str, Any] = entry.get("stats") or {}
    samples: Sequence[float] = entry.get("samples") or ()
    error = entry.get("error")
    trials = stats.get("trials")

    if trials == 0 or (trials is None and not _has_measurement(stats)):
        return GateResult(
            gate=LATENCY_GATE,
            status=UNKNOWN,
            subject=agent,
            detail=(
                f"{agent}: no successful probe, so reply latency was not "
                f"measured ({error or 'no reply'})"
            ),
            threshold=target_ms,
        )

    if strict:
        seconds, statistic = _tail_statistic(stats, samples)
    else:
        raw = stats.get("avg")
        statistic = "avg"
        try:
            seconds = None if raw is None else float(raw)
        except (TypeError, ValueError):
            seconds = None

    metric = f"{LATENCY_GATE}_{statistic}_ms"
    if seconds is None:
        return GateResult(
            gate=LATENCY_GATE,
            status=UNKNOWN,
            subject=agent,
            detail=f"{agent}: the probe reported no {statistic} to compare",
            threshold=target_ms,
        )

    value_ms = seconds * 1000.0
    if value_ms > target_ms:
        return GateResult(
            gate=LATENCY_GATE,
            status=WARN,
            subject=agent,
            detail=(
                f"{agent}: {metric} {value_ms:.0f} is over the {target_ms:.0f}ms target"
            ),
            metric=metric,
            value=value_ms,
            threshold=target_ms,
        )
    return GateResult(
        gate=LATENCY_GATE,
        status=PASS,
        subject=agent,
        detail=(
            f"{agent}: {metric} {value_ms:.0f} is within the {target_ms:.0f}ms target"
        ),
        metric=metric,
        value=value_ms,
        threshold=target_ms,
    )


def sfu_quality_gate(baseline: dict[str, Any] | None) -> GateResult:
    """Connection quality of the SFU baseline.

    ``Poor``/``Lost`` is a FAIL, which is ``check_json``'s reading;
    ``_verdict`` called the same input a WARN. Loss is not consulted: it is a
    hardcoded ``0.0`` (see the module docstring).
    """
    if not baseline:
        return GateResult(
            gate=SFU_QUALITY_GATE,
            status=UNKNOWN,
            detail="the SFU check returned no baseline measurement",
        )
    quality = baseline.get("quality")
    rtt = baseline.get("rtt_ms")
    if not quality or quality == _NO_QUALITY:
        return GateResult(
            gate=SFU_QUALITY_GATE,
            status=UNKNOWN,
            detail=(
                "the SFU baseline reported no connection quality "
                "(no client stayed connected long enough to read one)"
            ),
        )
    if quality in _DEGRADED_QUALITY:
        return GateResult(
            gate=SFU_QUALITY_GATE,
            status=FAIL,
            detail=f"SFU baseline connection quality is {quality} (rtt {rtt}ms)",
            metric="sfu_baseline_quality",
        )
    return GateResult(
        gate=SFU_QUALITY_GATE,
        status=PASS,
        detail=f"SFU baseline connection quality is {quality} (rtt {rtt}ms)",
        metric="sfu_baseline_quality",
    )


def _breaches(step: dict[str, Any], target_rtt_ms: float) -> bool:
    """Whether a ramp tier is outside budget. rtt and quality only, never loss."""
    if step.get("quality") in _DEGRADED_QUALITY:
        return True
    try:
        return float(step.get("rtt_ms") or 0.0) > target_rtt_ms
    except (TypeError, ValueError):
        return False


def sfu_capacity_gate(
    ramp: Sequence[dict[str, Any]],
    target_rtt_ms: float | None,
    resource: dict[str, Any] | None,
) -> GateResult:
    """Did the load ramp have any healthy capacity at all?

    This gate exists because ``find_knee`` returns ``None`` both when every tier
    stayed within budget and when the *first* tier already breached it. A reader
    that takes ``knee is None`` at face value calls a total failure a clean ramp.
    So the steps are read directly, and the only question asked is the one the
    ambiguity hides: was the smallest tier healthy?

    Finding a knee at a later tier is NOT a failure -- measuring where capacity
    ends is the entire purpose of running a ramp.
    """
    if not ramp:
        return GateResult(
            gate=SFU_CAPACITY_GATE,
            status=UNKNOWN,
            detail="the load ramp produced no steps, so capacity was not measured",
        )
    if target_rtt_ms is None:
        return GateResult(
            gate=SFU_CAPACITY_GATE,
            status=UNKNOWN,
            detail=(
                "no rtt threshold travelled with the ramp, so its steps cannot "
                "be compared against anything"
            ),
        )
    if resource is not None and resource.get("saturated") is True:
        return GateResult(
            gate=SFU_CAPACITY_GATE,
            status=UNKNOWN,
            detail=(
                "the prober host saturated during the ramp, so the curve "
                "describes this host and not the SFU"
            ),
            threshold=target_rtt_ms,
        )

    unmeasured_prober = resource is not None and resource.get("saturated") is None
    first = ramp[0]
    clients = first.get("clients")
    if _breaches(first, target_rtt_ms):
        caveat = (
            "; the prober's own load was not measured, so this host cannot be "
            "ruled out as the bottleneck"
            if unmeasured_prober
            else ""
        )
        return GateResult(
            gate=SFU_CAPACITY_GATE,
            status=FAIL,
            detail=(
                f"the smallest tier ({clients} clients) was already outside "
                f"budget (rtt {first.get('rtt_ms')}ms, quality "
                f"{first.get('quality')}) against a {target_rtt_ms}ms target, so "
                f"there is no healthy capacity to report{caveat}"
            ),
            metric="sfu_ramp_rtt_ms",
            value=_as_float(first.get("rtt_ms")),
            threshold=target_rtt_ms,
        )
    return GateResult(
        gate=SFU_CAPACITY_GATE,
        status=PASS,
        detail=(
            f"the smallest tier ({clients} clients) was within the "
            f"{target_rtt_ms}ms budget; read `knee` for where the curve degraded"
        ),
        metric="sfu_ramp_rtt_ms",
        value=_as_float(first.get("rtt_ms")),
        threshold=target_rtt_ms,
    )


def _as_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# The run-level entry point
# ---------------------------------------------------------------------------


def evaluate_checks(
    check_results: dict[str, Any], target_ms: float, *, strict: bool = False
) -> list[GateResult]:
    """Every gate for a set of executed checks, in check order.

    ``check_results`` is ``execute_run``'s shape: ``{check: {"ok": bool,
    "result": ...}}``. The CLI normalises its raw probe objects into the same
    shape (``report._check_results``) so both callers run this one function --
    that is the whole point of the collapse.

    A check that errored or timed out is a FAIL, which is ``_verdict``'s reading
    and the stricter of the two. That is distinct from a check that *succeeded*
    and measured nothing, which is UNKNOWN: one says the probe broke, the other
    says the probe worked and found nothing to judge.
    """
    gates: list[GateResult] = []
    for name, entry in check_results.items():
        if not entry.get("ok"):
            gates.append(
                GateResult(
                    gate=f"check:{name}",
                    status=FAIL,
                    detail=(
                        f"the {name} check did not complete: "
                        f"{entry.get('error') or 'unknown error'}"
                    ),
                )
            )
            continue
        result: dict[str, Any] = entry.get("result") or {}
        if name == "agents":
            gates.append(agents_gate(result))
        elif name in ("sfu", "sfu_load"):
            gates.append(sfu_quality_gate(result.get("baseline")))
            if name == "sfu_load":
                gates.append(
                    sfu_capacity_gate(
                        result.get("ramp") or (),
                        _as_float(result.get("target_rtt_ms")),
                        result.get("resource"),
                    )
                )
        elif name == "latency":
            gates.extend(
                latency_gates(result.get("agents") or (), target_ms, strict=strict)
            )
        else:
            gates.append(
                GateResult(
                    gate=f"check:{name}",
                    status=UNKNOWN,
                    detail=(
                        f"no gate knows how to read the {name} check, so its "
                        "result was not judged"
                    ),
                )
            )
    return gates


def summary_lines(gates: Sequence[GateResult]) -> list[str]:
    """One ``[STATUS] gate: detail`` line per gate, for a human or a CI log."""
    return [f"  [{g.status}] {g.gate}: {g.detail}" for g in gates]


__all__ = [
    "AGENTS_GATE",
    "FAIL",
    "LATENCY_GATE",
    "MIN_PERCENTILE_SAMPLES",
    "PASS",
    "SFU_CAPACITY_GATE",
    "SFU_QUALITY_GATE",
    "UNKNOWN",
    "WARN",
    "GateResult",
    "agents_gate",
    "evaluate_checks",
    "exit_code",
    "latency_gates",
    "sfu_capacity_gate",
    "sfu_quality_gate",
    "summary_lines",
    "verdict",
    "worst_status",
]
