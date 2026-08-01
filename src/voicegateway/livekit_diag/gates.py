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
from dataclasses import asdict, dataclass, replace
from typing import Any

from voicegateway.utils.percentiles import compute_percentiles

PASS = "PASS"
WAIVED = "WAIVED"
WARN = "WARN"
UNKNOWN = "UNKNOWN"
FAIL = "FAIL"

# Severity, worst last. UNKNOWN outranks WARN on purpose: a WARN is bounded
# knowledge ("1.8s against your 1.5s target"), while a gate that could not
# evaluate is unbounded ("it might be 30s, or the agent might be dead"). The
# run's verdict is the worst gate, so a run that both measured a degradation and
# failed to measure something reports the un-measured half, which is the part
# nobody can act on until it is fixed.
# WAIVED sits directly above PASS and below WARN. Above PASS because a gate
# nobody enforced has not been satisfied, and collapsing it into PASS is exactly
# the silent pass the waiver exists to prevent. Below WARN because a WARN is an
# observed degradation, which is worse news than a threshold somebody explicitly
# and in writing agreed not to hold this run to.
_SEVERITY = {PASS: 0, WAIVED: 1, WARN: 2, UNKNOWN: 3, FAIL: 4}

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
ESTABLISHMENT_GATE = "call_establishment"
NODE_CPU_GATE = "node_cpu"
NODE_MEMORY_GATE = "node_memory"
HEADROOM_GATE = "resource_headroom"
RETURN_TO_BASELINE_GATE = "return_to_baseline"

# The two Go runtime series the teardown check reads. RSS is deliberately absent:
# Go hands freed heap back to the OS lazily, so a drained process holds its
# resident size long after the heap emptied, and gating on it would report a leak
# on healthy runs.
BASELINE_HEAP = "heap_inuse_bytes"
BASELINE_GOROUTINES = "go_goroutines"

# How long teardown must have been left to settle before a post sample means
# anything. The runbook specifies 5 to 10 minutes, past LiveKit's reap timeouts;
# this is the floor of that range, so a sample taken earlier is not a
# post-settle sample and this module refuses to read it as one.
MIN_SETTLE_MS = 300_000

# The share of call attempts that must establish. Inclusive: a run landing
# exactly on the bar passes. This is an acceptance threshold, not a tuning knob,
# which is why it lives beside the gate ids rather than in a config file.
MIN_ESTABLISHMENT_RATIO = 0.995

# Per-node resource ceilings. These are STRICT, unlike MIN_ESTABLISHMENT_RATIO
# above: the criterion reads "CPU below 70%, memory below 75%", so a node sitting
# exactly on its ceiling has not stayed below it and does not pass. The two
# directions live next to each other deliberately, because reusing one gate's
# comparison for the other is the easy mistake.
MAX_NODE_CPU_UTILISATION = 0.70
MAX_NODE_MEMORY_UTILISATION = 0.75

# Headroom that must REMAIN on a limited resource. A floor, and inclusive: a node
# sitting on exactly 20% free has met "at least 20% headroom".
#
# This is not the 0.85 that appears in the node-count sizing formula, and the two
# must never be reconciled. That 0.85 is a 15% CPU margin reserved so the fleet
# still carries its target after losing a node; this 0.20 is how much of a
# limited resource has to be unused during the run. They measure different
# things, on different resources, for different reasons. Expressing this one as
# headroom rather than as an 0.80 utilisation ceiling is deliberate: the moment
# it is written as 0.80, somebody reads it next to 0.85 and "corrects" one of
# them.
MIN_HEADROOM_FRACTION = 0.20

# The resources the criterion names. Only the first is scraped today; the other
# two are carried so the report cannot go quiet about them.
HEADROOM_FILE_DESCRIPTORS = "file_descriptors"
HEADROOM_RTP_PORTS = "rtp_ports"
HEADROOM_NETWORK = "network"


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
    # Set only by :func:`waive`, and never empty when it is set. Carried as a
    # field as well as inside ``detail`` so a surface that renders structured
    # data rather than prose still has to deal with it.
    waiver_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """A JSON-safe copy (the run payload is persisted and served as JSON).

        ``waiver_reason`` is omitted entirely when it is None, so the payload of
        every gate that was not waived keeps exactly the shape it had before
        waivers existed. That shape is a contract: it is persisted with each run,
        served to the dashboard, and pinned by a test that asserts the key set.
        A waived gate is the only one that carries new information, so it is the
        only one whose payload grows.
        """
        out = asdict(self)
        if out.get("waiver_reason") is None:
            out.pop("waiver_reason", None)
        return out


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
    # WAIVED is not clean either. A waived gate is one nobody held the run to,
    # which is a decision worth a non-zero exit: a pipeline that goes green on a
    # waiver has quietly dropped the requirement rather than recorded it.
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

    ``quality`` and ``rtt_ms`` are INDEPENDENT readings, and this gate used to
    consult only the first. ``quality`` is the SDK's own peer-connection metric;
    ``rtt_ms`` is a mean over ping round trips, which needs a pong back. They
    disagree exactly when the connection came up and every ping timed out: that
    baseline reports ``Excellent`` beside an rtt of ``0.0`` over zero samples,
    and ``Excellent`` is neither falsy, nor :data:`_NO_QUALITY`, nor in
    :data:`_DEGRADED_QUALITY`, so it fell through to PASS while the detail line
    printed ``rtt 0.0ms`` as though that were a time. So the gate certified a
    healthy baseline for a run in which not one round trip completed: the same
    defect :func:`sfu_capacity_gate` had for the ramp.

    A baseline that measured nothing (:func:`_unmeasured_reason`, the helper the
    ramp already uses, so the two surfaces cannot drift) is now :data:`UNKNOWN`
    whatever ``quality`` says. UNKNOWN and not FAIL, for the ramp's reason:
    nothing was observed, so nothing is claimed, and the pings can die because
    the PROBER never got its own client connected. It still exits non-zero and
    still outranks WARN.

    A ``Poor``/``Lost`` baseline that measured no rtt is still a FAIL, also
    matching the ramp: a degraded quality is a real observation of the SFU,
    while a missing round trip is only an absence. Its rtt is not a reading
    either way, so no branch prints one it does not have.
    """
    if not baseline:
        return GateResult(
            gate=SFU_QUALITY_GATE,
            status=UNKNOWN,
            detail="the SFU check returned no baseline measurement",
        )
    quality = baseline.get("quality")
    rtt = baseline.get("rtt_ms")
    unmeasured = _unmeasured_reason(baseline)
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
        observed = (
            f"no rtt reading: {unmeasured}" if unmeasured is not None else f"rtt {rtt}ms"
        )
        return GateResult(
            gate=SFU_QUALITY_GATE,
            status=FAIL,
            detail=f"SFU baseline connection quality is {quality} ({observed})",
            metric="sfu_baseline_quality",
        )
    if unmeasured is not None:
        return GateResult(
            gate=SFU_QUALITY_GATE,
            status=UNKNOWN,
            detail=(
                f"the SFU baseline measured nothing: {unmeasured}, so its "
                f"connection quality of {quality} is the only signal and no "
                "round trip through the SFU was completed at all"
            ),
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


def _sample_count(step: dict[str, Any]) -> int | None:
    """``samples`` off a ramp step or baseline, or ``None`` when it carries none.

    Absent is NOT zero. Runs stored before ``sfu.RampStep`` grew a sample count
    have no such key, and reading that absence as "measured nothing" would
    retroactively turn every archived run UNKNOWN, healthy ones included.
    """
    if "samples" not in step:
        return None
    raw = step.get("samples")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _unmeasured_reason(step: dict[str, Any]) -> str | None:
    """Why a ramp tier or baseline is not a measurement, or ``None`` when it is.

    One helper for both, deliberately: :func:`sfu_capacity_gate` and
    :func:`sfu_quality_gate` read the same ``{rtt_ms, quality, samples}`` shape
    off the same probe, so a second copy of this rule is a second place for it
    to drift.

    Keyed on ``samples``, the count of ping round-trips that came back, because
    a count is a fact while the ``rtt_ms`` of ``0.0`` a reading reports when
    every ping timed out is an artifact of averaging an empty list. Nothing else
    here could catch that: ``0.0 > target`` is False, and the quality a tier
    reports in that state is :data:`_NO_QUALITY`, which is deliberately not in
    :data:`_DEGRADED_QUALITY` -- while a BASELINE in that state can report a
    perfectly healthy ``Excellent``, since its quality comes from the SDK's
    peer-connection metric rather than from the pings. So a run where every
    single ping timed out used to satisfy both gates and report PASS -- a
    monitoring tool telling an operator a dead SFU is fine.

    A reading with no ``samples`` key at all is NOT assumed unmeasured: runs
    stored before the count existed have no such key, and reading absence as
    zero would turn every archived run UNKNOWN. Those fall back to the number
    itself, under the rule :func:`_has_measurement` already applies to reply
    latency: a round trip through an SFU cannot take zero milliseconds, so a
    non-positive (or missing) rtt on a reading that carries no count is the
    placeholder, and any positive rtt is judged exactly as it was before.
    """
    count = _sample_count(step)
    if count is not None:
        return None if count > 0 else "no ping round-trip came back (samples 0)"
    rtt = _as_float(step.get("rtt_ms"))
    if rtt is None or rtt <= 0.0:
        return (
            "it carries no sample count (a run stored before they were recorded) "
            f"and its rtt of {step.get('rtt_ms')} is not a round-trip time"
        )
    return None


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

    A smallest tier that measured NOTHING (see :func:`_unmeasured_reason`) is
    :data:`UNKNOWN`. It used to be a PASS, which is the same class of bug as the
    ``knee is None`` ambiguity this gate exists to close, in a worse direction:
    the reading came from a server where every ping timed out.
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
    unmeasured = _unmeasured_reason(first)
    degraded = first.get("quality") in _DEGRADED_QUALITY
    if unmeasured is not None and not degraded:
        # Nothing came back, and no quality reading indicts the SFU either, so
        # there is no number to compare and no observed failure to report.
        # UNKNOWN, not FAIL: FAIL would claim a breach nobody watched, and the
        # tier may have measured nothing because the PROBER could not connect
        # its own clients. UNKNOWN still exits non-zero (see exit_code) and
        # still outranks WARN in the run verdict, so this is not the soft
        # option: it is the accurate one.
        return GateResult(
            gate=SFU_CAPACITY_GATE,
            status=UNKNOWN,
            detail=(
                f"the smallest tier ({clients} clients) measured nothing: "
                f"{unmeasured}, so there is no rtt to compare against the "
                f"{target_rtt_ms}ms budget and no capacity was demonstrated"
            ),
            threshold=target_rtt_ms,
        )
    if _breaches(first, target_rtt_ms):
        caveat = (
            "; the prober's own load was not measured, so this host cannot be "
            "ruled out as the bottleneck"
            if unmeasured_prober
            else ""
        )
        # A degraded tier that measured no rtt is still a FAIL -- Poor/Lost is an
        # observation -- but its rtt of 0.0 is not, so it is neither printed as a
        # reading nor published as the gate's value.
        observed = (
            f"quality {first.get('quality')}, and no rtt reading: {unmeasured}"
            if unmeasured is not None
            else f"rtt {first.get('rtt_ms')}ms, quality {first.get('quality')}"
        )
        return GateResult(
            gate=SFU_CAPACITY_GATE,
            status=FAIL,
            detail=(
                f"the smallest tier ({clients} clients) was already outside "
                f"budget ({observed}) against a {target_rtt_ms}ms target, so "
                f"there is no healthy capacity to report{caveat}"
            ),
            metric=None if unmeasured is not None else "sfu_ramp_rtt_ms",
            value=None if unmeasured is not None else _as_float(first.get("rtt_ms")),
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


def _as_count(value: Any) -> int | None:
    """A call count, or ``None`` when the value cannot be one.

    ``bool`` is refused explicitly. It is an ``int`` subclass in Python, so a
    stray ``True`` would otherwise arrive as an attempt count of 1 and produce a
    confident rate out of a flag.

    ``OverflowError`` is caught alongside the usual pair because it is reachable
    from a real artifact, not just in theory: :func:`json.loads` accepts the
    non-standard ``Infinity`` token by default, so a malformed summary can hand
    this function ``float('inf')``, and ``int(float('inf'))`` raises
    ``OverflowError`` rather than ``ValueError``. An unparseable count is an
    UNKNOWN verdict, never a traceback out of a gate.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def establishment_gate(
    *,
    attempted: Any,
    succeeded: Any,
    threshold: float = MIN_ESTABLISHMENT_RATIO,
    subject: str | None = None,
) -> GateResult:
    """Did enough call attempts actually establish?

    The acceptance criterion is at least 99.5% establishment. The comparison is
    inclusive, so a run landing exactly on the bar passes.

    COUNTS, NEVER A RATIO SOMEBODY ELSE COMPUTED. A load generator publishes its
    own success ratio and, separately, its own pass/fail self-assessment against
    its own configured bar. Neither is this gate's answer. Reading a generator's
    verdict back out as the verdict is a run grading its own homework, and when
    the two bars happen to coincide the substitution is invisible in the output.
    Callers extract ``attempted`` and ``succeeded``; this function decides.

    Unmeasured is never a pass, following the rule :func:`_unmeasured_reason`
    applies to the SFU readings. The specific trap UNKNOWN exists to catch here:
    a run that placed zero calls also recorded zero failures, so any gate phrased
    as "failures within budget" calls that run perfect. Absent counts, zero
    attempts, and counts that cannot describe a run (negative, or more
    successes than attempts) all return UNKNOWN with the reason, never PASS.

    Synchronous, like every gate in this module. Do not await it.
    """
    a = _as_count(attempted)
    s = _as_count(succeeded)
    if a is None or s is None:
        which = "attempt and success counts"
        if a is not None:
            which = "success count"
        elif s is not None:
            which = "attempt count"
        return GateResult(
            gate=ESTABLISHMENT_GATE,
            status=UNKNOWN,
            subject=subject,
            detail=(
                f"the run reported no {which}, so no establishment rate could "
                "be computed and no establishment was demonstrated"
            ),
            threshold=threshold,
        )
    if a <= 0:
        return GateResult(
            gate=ESTABLISHMENT_GATE,
            status=UNKNOWN,
            subject=subject,
            detail=(
                "the run reported 0 call attempts, so there is no establishment "
                "rate to judge. It recorded no failures either, which is why "
                "this is UNKNOWN rather than a pass"
            ),
            threshold=threshold,
        )
    if s < 0 or s > a:
        return GateResult(
            gate=ESTABLISHMENT_GATE,
            status=UNKNOWN,
            subject=subject,
            detail=(
                f"the counts do not describe a run ({s} established of {a} "
                "attempted), so the rate they imply is not a measurement"
            ),
            threshold=threshold,
        )
    ratio = s / a
    status = PASS if ratio >= threshold else FAIL
    lead = (
        f"{s} of {a} call attempts established"
        if status == PASS
        else f"only {s} of {a} call attempts established"
    )
    relation = "at or above" if status == PASS else "below"
    return GateResult(
        gate=ESTABLISHMENT_GATE,
        status=status,
        subject=subject,
        detail=(
            f"{lead} ({ratio * 100:.3f}%), {relation} the {threshold * 100:.1f}% bar"
        ),
        metric="establishment_ratio",
        value=ratio,
        threshold=threshold,
    )


@dataclass(frozen=True)
class NodeUtilisationReading:
    """One node's peak utilisation over a window, as a fraction, or nothing.

    ``utilisation`` is ``None`` whenever the window produced no usable reading,
    and ``unmeasured_reason`` says why. None is never 0.0: an idle node reads
    0.0, and a node nobody could scrape reads None.

    Assembled by the caller, not here, because building it is a measurement and
    this module only judges. CPU comes from
    ``node_samples_repository.utilisation_points`` over the paired
    ``cpu_seconds_total`` and ``cpu_idle_seconds_total`` rates; memory comes from
    the gauge pair, where peak utilisation corresponds to the MINIMUM of
    ``memory_available_bytes`` rather than its maximum.

    ``samples`` counts the points that produced a usable fraction, so a peak over
    two readings is never presented as if it came from two hundred.
    """

    node: str
    utilisation: float | None
    samples: int = 0
    source: str | None = None
    unmeasured_reason: str | None = None


def _resource_gate(
    gate_id: str,
    what: str,
    reading: NodeUtilisationReading,
    threshold: float,
) -> GateResult:
    """One node judged against a strict ceiling. See :func:`node_cpu_gates`."""
    subject = f"{reading.node}/{reading.source}" if reading.source else reading.node
    if reading.utilisation is None:
        why = reading.unmeasured_reason or "the window produced no usable reading"
        return GateResult(
            gate=gate_id,
            status=UNKNOWN,
            subject=subject,
            detail=(
                f"{what} on {subject} was not measured: {why}. No ceiling was "
                "demonstrated, which is not the same as staying under one"
            ),
            threshold=threshold,
        )
    # Strict: the criterion is "below", so sitting exactly on the ceiling is not
    # under it.
    status = PASS if reading.utilisation < threshold else FAIL
    relation = "below" if status == PASS else "at or above"
    return GateResult(
        gate=gate_id,
        status=status,
        subject=subject,
        detail=(
            f"peak {what} on {subject} was {reading.utilisation * 100:.1f}% "
            f"over {reading.samples} measured sample(s), {relation} the "
            f"{threshold * 100:.0f}% ceiling"
        ),
        metric=f"{gate_id}_utilisation",
        value=reading.utilisation,
        threshold=threshold,
    )


def _resource_gates(
    gate_id: str,
    what: str,
    readings: Sequence[NodeUtilisationReading],
    threshold: float,
) -> list[GateResult]:
    if not readings:
        return [
            GateResult(
                gate=gate_id,
                status=UNKNOWN,
                detail=(
                    f"no node was sampled in the window, so no {what} ceiling "
                    "was demonstrated. A window with no samples is not a quiet "
                    "fleet"
                ),
                threshold=threshold,
            )
        ]
    return [_resource_gate(gate_id, what, r, threshold) for r in readings]


def node_cpu_gates(
    readings: Sequence[NodeUtilisationReading],
    *,
    threshold: float = MAX_NODE_CPU_UTILISATION,
) -> list[GateResult]:
    """One gate per node: did peak CPU stay below the ceiling?

    Per node, never aggregated. A mean across a fleet hides the one node that
    saturated, and the criterion is written per node.

    STRICT comparison, unlike :func:`establishment_gate`: "CPU below 70%" is not
    satisfied by sitting on 70%.

    A node whose window produced no usable reading is UNKNOWN, never PASS. The
    two window states this exists for are the ones
    ``node_correlation_repository`` names: ``scrape_failed`` (every scrape in the
    window errored, so nothing is known) and ``no_samples`` (nobody scraped that
    window at all). Neither is a quiet node. The caller decides which applies by
    branching on the sample ``outcome`` counts, never on a value being NULL,
    because every value column of ``node_samples`` is nullable and a NULL there
    means "not measured" rather than zero.

    Synchronous, like every gate in this module. The correlation and rate reads
    that produce ``readings`` are async and happen in the caller.
    """
    return _resource_gates(NODE_CPU_GATE, "CPU", readings, threshold)


def node_memory_gates(
    readings: Sequence[NodeUtilisationReading],
    *,
    threshold: float = MAX_NODE_MEMORY_UTILISATION,
) -> list[GateResult]:
    """One gate per node: did peak memory stay below the ceiling?

    Same rules as :func:`node_cpu_gates`, at a different ceiling. Note that peak
    memory utilisation comes from the MINIMUM of ``memory_available_bytes`` over
    the window, not its maximum: the most-used instant is the least-available
    one.
    """
    return _resource_gates(NODE_MEMORY_GATE, "memory", readings, threshold)


@dataclass(frozen=True)
class HeadroomReading:
    """How much of one limited resource was still free on one node.

    ``used`` and ``limit`` are raw counts in the resource's own unit, not a
    fraction, so the detail line can say "819200 of 1048576 file descriptors"
    rather than a percentage a reader cannot check. Either being ``None`` means
    the pair was not measured, and ``unmeasured_reason`` says why.

    File descriptors come from the ``filefd_allocated`` / ``filefd_maximum``
    gauge pair. RTP ports and network are named by the criterion and scraped by
    nothing today, which is what :func:`unscraped_headroom_readings` exists for.
    """

    node: str
    resource: str
    used: float | None
    limit: float | None
    source: str | None = None
    unmeasured_reason: str | None = None


def unscraped_headroom_readings(
    node: str, *, source: str | None = None
) -> list[HeadroomReading]:
    """The headroom resources nothing measures yet, as explicit non-readings.

    The criterion asks for headroom on network, RTP ports and system limits.
    Only system limits (file descriptors) are scraped. Emitting the other two as
    ``not_measured`` is the whole point of this helper: a report that simply
    omits them shows one green row and reads as full coverage of a three-part
    requirement.

    A helper rather than a note in a docstring, because a caller that has to
    remember to add them is a caller that eventually does not.
    """
    return [
        HeadroomReading(
            node=node,
            resource=resource,
            used=None,
            limit=None,
            source=source,
            unmeasured_reason=reason,
        )
        for resource, reason in (
            (
                HEADROOM_RTP_PORTS,
                "no exporter in the scrape set publishes an RTP port-range "
                "usage series, so nothing measures it",
            ),
            (
                HEADROOM_NETWORK,
                "no exporter in the scrape set publishes a network saturation "
                "series (PPS or conntrack), so nothing measures it",
            ),
        )
    ]


def _headroom_gate(reading: HeadroomReading, threshold: float) -> GateResult:
    subject = f"{reading.node}/{reading.resource}"
    if reading.used is None or reading.limit is None:
        why = reading.unmeasured_reason or "the window produced no usable reading"
        return GateResult(
            gate=HEADROOM_GATE,
            status=UNKNOWN,
            subject=subject,
            detail=(
                f"{reading.resource} headroom on {reading.node} was not "
                f"measured: {why}. Absent headroom evidence is not spare "
                "capacity"
            ),
            threshold=threshold,
        )
    if reading.limit <= 0 or reading.used < 0 or reading.used > reading.limit:
        return GateResult(
            gate=HEADROOM_GATE,
            status=UNKNOWN,
            subject=subject,
            detail=(
                f"the {reading.resource} counts on {reading.node} do not "
                f"describe a limit ({reading.used:.0f} of {reading.limit:.0f}), "
                "so the headroom they imply is not a measurement"
            ),
            threshold=threshold,
        )
    free = reading.limit - reading.used
    # (limit - used) / limit, NOT 1 - used/limit. The two are equal in algebra and
    # not in float: 1 - 800/1000 is 0.19999999999999996, which is below a 0.20
    # floor, so a node sitting exactly on the bar would be reported as breaching
    # it. Subtracting the counts first keeps the boundary exact.
    headroom = free / reading.limit
    # Inclusive floor: "at least 20% headroom" is met by exactly 20%. This is the
    # opposite direction to the CPU and memory ceilings above, which are strict.
    # Compared as counts for the same reason as above: free >= threshold * limit
    # cannot drift the way a difference of two fractions can.
    status = PASS if free >= threshold * reading.limit else FAIL
    relation = "at or above" if status == PASS else "below"
    return GateResult(
        gate=HEADROOM_GATE,
        status=status,
        subject=subject,
        detail=(
            f"{reading.resource} on {reading.node} had "
            f"{headroom * 100:.1f}% headroom ({reading.used:.0f} of "
            f"{reading.limit:.0f} used), {relation} the "
            f"{threshold * 100:.0f}% floor"
        ),
        metric=f"{reading.resource}_headroom",
        value=headroom,
        threshold=threshold,
    )


def headroom_gates(
    readings: Sequence[HeadroomReading],
    *,
    threshold: float = MIN_HEADROOM_FRACTION,
) -> list[GateResult]:
    """One gate per (node, resource): did enough of the limit stay free?

    A FLOOR, and inclusive, unlike :func:`node_cpu_gates`. Exactly 20% free
    satisfies "at least 20% headroom".

    ``threshold`` is headroom, never a utilisation ceiling. See
    :data:`MIN_HEADROOM_FRACTION` for why that distinction is load-bearing and
    why the sizing formula's 0.85 must never be reconciled with it.

    Synchronous, like every gate here.
    """
    if not readings:
        return [
            GateResult(
                gate=HEADROOM_GATE,
                status=UNKNOWN,
                detail=(
                    "no headroom reading was supplied, so no limit was shown to "
                    "have room left. Nothing measured is not room to spare"
                ),
                threshold=threshold,
            )
        ]
    return [_headroom_gate(r, threshold) for r in readings]


@dataclass(frozen=True)
class BaselineComparison:
    """One node's reading for one metric, idle before against settled after.

    ``metric`` is :data:`BASELINE_HEAP` or :data:`BASELINE_GOROUTINES`. Both are
    raw values in their own unit, so the detail can quote them.

    The two ``*_at_ms`` timestamps are optional but load-bearing when present:
    they are what lets the gate refuse a "post-settle" sample that was taken
    before the settle window elapsed.
    """

    node: str
    metric: str
    baseline: float | None
    post_settle: float | None
    baseline_at_ms: int | None = None
    post_settle_at_ms: int | None = None
    source: str | None = None
    unmeasured_reason: str | None = None


def _return_to_baseline_gate(
    comparison: BaselineComparison, tolerance: float, min_settle_ms: int
) -> GateResult:
    c = comparison
    subject = f"{c.node}/{c.metric}"
    if c.baseline is None or c.post_settle is None:
        why = c.unmeasured_reason or "one of the two samples is missing"
        return GateResult(
            gate=RETURN_TO_BASELINE_GATE,
            status=UNKNOWN,
            subject=subject,
            detail=(
                f"{c.metric} on {c.node} could not be compared against its "
                f"baseline: {why}. Nothing was shown to have been given back"
            ),
            threshold=tolerance,
        )
    if c.baseline <= 0:
        return GateResult(
            gate=RETURN_TO_BASELINE_GATE,
            status=UNKNOWN,
            subject=subject,
            detail=(
                f"the {c.metric} baseline on {c.node} is {c.baseline:g}, which "
                "is not a level to return to, so no ratio is meaningful"
            ),
            threshold=tolerance,
        )
    if c.baseline_at_ms is not None and c.post_settle_at_ms is not None:
        settled_ms = c.post_settle_at_ms - c.baseline_at_ms
        if settled_ms < min_settle_ms:
            return GateResult(
                gate=RETURN_TO_BASELINE_GATE,
                status=UNKNOWN,
                subject=subject,
                detail=(
                    f"the second {c.metric} sample on {c.node} was taken "
                    f"{settled_ms / 1000:.0f}s after the first, inside the "
                    f"{min_settle_ms / 1000:.0f}s settle window, so it is not a "
                    "post-settle reading and a return cannot be claimed from it"
                ),
                threshold=tolerance,
            )
    ratio = c.post_settle / c.baseline
    status = PASS if ratio <= tolerance else FAIL
    relation = "within" if status == PASS else "outside"
    return GateResult(
        gate=RETURN_TO_BASELINE_GATE,
        status=status,
        subject=subject,
        detail=(
            f"{c.metric} on {c.node} settled at {c.post_settle:g} against a "
            f"baseline of {c.baseline:g} ({ratio:.2f}x), {relation} the "
            f"{tolerance:.2f}x tolerance"
        ),
        metric=f"{c.metric}_baseline_ratio",
        value=ratio,
        threshold=tolerance,
    )


def return_to_baseline_gates(
    comparisons: Sequence[BaselineComparison],
    *,
    tolerance: float,
    min_settle_ms: int = MIN_SETTLE_MS,
) -> list[GateResult]:
    """Did the fleet give its resources back after teardown?

    ``tolerance`` IS REQUIRED and has no default, on purpose. The acceptance
    criterion says resources must return "near baseline" and never quantifies
    "near", so there is no contracted number for this module to hold. Inventing
    a default here would put a number nobody agreed to into a report while
    looking exactly like the thresholds that were agreed. The caller has to state
    it, and it travels on every result as ``threshold`` so a reader sees which
    tolerance produced the verdict.

    ``tolerance`` is a ratio ceiling: 1.5 means the settled value may be at most
    1.5x the idle baseline.

    A post sample taken before ``min_settle_ms`` elapsed is UNKNOWN, not PASS.
    Teardown that has not finished draining can look like a clean return, and
    that reads as evidence when it is the absence of it.

    Reads ``heap_inuse_bytes`` and ``go_goroutines``, never RSS. See
    :data:`BASELINE_HEAP`.

    Synchronous, like every gate here.
    """
    if not comparisons:
        return [
            GateResult(
                gate=RETURN_TO_BASELINE_GATE,
                status=UNKNOWN,
                detail=(
                    "no before-and-after pair was supplied, so nothing was "
                    "shown to have returned to baseline. An absent comparison "
                    "is not a clean teardown"
                ),
                threshold=tolerance,
            )
        ]
    return [_return_to_baseline_gate(c, tolerance, min_settle_ms) for c in comparisons]


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


def waive(gate: GateResult, *, reason: str) -> GateResult:
    """Record a gate as explicitly waived, with the reason it was waived.

    For the case the checklist names: a threshold the run was not held to,
    because somebody decided in writing not to hold it. The requirement is that
    this is recorded, never that it becomes a pass.

    ``reason`` is required and may not be blank. A waiver with no reason is
    indistinguishable from a gate somebody quietly deleted, which is the thing
    this function exists to make impossible. Whitespace does not count.

    The reason lands in two places on purpose: on
    :attr:`GateResult.waiver_reason` for a structured consumer, and prefixed into
    ``detail`` for every surface that renders prose. A surface that shows one and
    not the other still shows the reason.

    WAIVED never collapses to PASS. It outranks PASS in :data:`_SEVERITY`, so a
    run whose only blemish is a waiver reports WAIVED rather than PASS, and
    :func:`exit_code` returns non-zero for it.

    The original status is kept in the detail rather than discarded: "it would
    have failed and we waived it" and "it would have passed anyway" are
    different facts, and only the first one is a risk somebody accepted.
    """
    if not reason or not reason.strip():
        raise ValueError(
            "a waiver requires a reason: an unexplained waiver is "
            "indistinguishable from a silently dropped gate"
        )
    reason = reason.strip()
    return replace(
        gate,
        status=WAIVED,
        detail=f"WAIVED ({reason}). Would otherwise have been {gate.status}: {gate.detail}",
        waiver_reason=reason,
    )


def summary_lines(gates: Sequence[GateResult]) -> list[str]:
    """One ``[STATUS] gate: detail`` line per gate, for a human or a CI log."""
    return [f"  [{g.status}] {g.gate}: {g.detail}" for g in gates]


__all__ = [
    "AGENTS_GATE",
    "BASELINE_GOROUTINES",
    "BASELINE_HEAP",
    "ESTABLISHMENT_GATE",
    "FAIL",
    "HEADROOM_FILE_DESCRIPTORS",
    "HEADROOM_GATE",
    "HEADROOM_NETWORK",
    "HEADROOM_RTP_PORTS",
    "LATENCY_GATE",
    "MAX_NODE_CPU_UTILISATION",
    "MAX_NODE_MEMORY_UTILISATION",
    "MIN_ESTABLISHMENT_RATIO",
    "MIN_HEADROOM_FRACTION",
    "MIN_PERCENTILE_SAMPLES",
    "MIN_SETTLE_MS",
    "NODE_CPU_GATE",
    "NODE_MEMORY_GATE",
    "RETURN_TO_BASELINE_GATE",
    "PASS",
    "SFU_CAPACITY_GATE",
    "SFU_QUALITY_GATE",
    "UNKNOWN",
    "WAIVED",
    "WARN",
    "BaselineComparison",
    "GateResult",
    "HeadroomReading",
    "NodeUtilisationReading",
    "agents_gate",
    "establishment_gate",
    "evaluate_checks",
    "exit_code",
    "headroom_gates",
    "latency_gates",
    "node_cpu_gates",
    "node_memory_gates",
    "return_to_baseline_gates",
    "unscraped_headroom_readings",
    "sfu_capacity_gate",
    "sfu_quality_gate",
    "summary_lines",
    "verdict",
    "waive",
    "worst_status",
]
