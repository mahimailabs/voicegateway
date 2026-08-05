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
SUSTAINED_HEALTH_GATE = "sustained_health"
PROCESS_LIFECYCLE_GATE = "process_lifecycle"
RESOURCE_TREND_GATE = "resource_trend"
NETWORK_ALLOWANCE_GATE = "network_allowance"
TWO_WAY_MEDIA_GATE = "two_way_media"

#: The subject a gate is filed under when it describes the whole fleet rather
#: than one node. Used where nothing was sampled at all: the finding is that NO
#: node reported, so naming one would be wrong, and leaving it blank prints an
#: empty cell in a table where every other row identifies itself.
FLEET_SUBJECT = "fleet"

#: Gates whose ``value`` and ``threshold`` are FRACTIONS in 0..1 that a reader
#: thinks of as percentages. Every other gate carries an absolute: the latency
#: and SFU gates are milliseconds.
#:
#: Declared here rather than inferred by a renderer, for two reasons. The
#: distinction is a fact about what each gate measures, which is this module's
#: business and not a display concern. And it cannot be recovered from the
#: metric NAME: an unmeasured headroom gate carries ``metric=None`` and a real
#: 0.2 threshold, so a name-based rule would render the contracted 20% as "0.2".
#:
#: A renderer that treats these as plain numbers misstates the contract. 0.995
#: at one decimal place reads "1.0" and 0.75 reads "0.8", which is FIVE POINTS
#: PERMISSIVE on the memory ceiling in the document whose purpose is to show the
#: ceiling was held.
RATIO_GATES: frozenset[str] = frozenset(
    {
        ESTABLISHMENT_GATE,
        NODE_CPU_GATE,
        NODE_MEMORY_GATE,
        HEADROOM_GATE,
        RETURN_TO_BASELINE_GATE,
    }
)

#: Every gate id, so a new one has to be classified rather than defaulting into
#: whichever branch a renderer happens to fall through to.
ALL_GATES: frozenset[str] = frozenset(
    {
        AGENTS_GATE,
        LATENCY_GATE,
        SFU_QUALITY_GATE,
        SFU_CAPACITY_GATE,
        ESTABLISHMENT_GATE,
        NODE_CPU_GATE,
        NODE_MEMORY_GATE,
        HEADROOM_GATE,
        RETURN_TO_BASELINE_GATE,
        # Registered here and deliberately ABSENT from RATIO_GATES above: its
        # value is a count of consecutive failed samples, not a fraction, and
        # rendering 3 as "300%" is exactly the misstatement that set prevents.
        SUSTAINED_HEALTH_GATE,
        # Counts too: restarts and OOM kills, and a slope in absolute units.
        # Same reason, same exclusion from RATIO_GATES.
        PROCESS_LIFECYCLE_GATE,
        RESOURCE_TREND_GATE,
        # A count of allowance-exceeded EVENTS over the window, so the same
        # exclusion again. "9613" rendered as "961300%" would be the exact
        # misstatement RATIO_GATES exists to prevent, and this gate is one where
        # the number is quoted back to a client as evidence of throttling.
        NETWORK_ALLOWANCE_GATE,
        # A count of calls, not a fraction, so excluded from RATIO_GATES for the
        # same reason as the three above.
        TWO_WAY_MEDIA_GATE,
    }
)

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

# The smallest baseline a RATIO may be taken against.
#
# Observed: monitor-0 finished a run with sockstat_udp_inuse at 7 against an idle
# baseline of 3 and was reported FAIL at 2.33x, outside the 1.10x tolerance. That
# is four sockets on the box running the collector, which carried no test load at
# all. Nothing leaked; the denominator was simply too small for a ratio to mean
# anything, and at a baseline of 3 the smallest possible movement is already
# 1.33x.
#
# This is the same defect MIN_TREND_DRIFT_FRACTION exists to prevent one gate
# over, and it is expressed differently for a reason. A trend has a magnitude
# floor because its metrics share no scale. Here the metrics are all COUNTS or
# BYTES that only ever grow from a floor of zero, so the honest guard is on the
# denominator: below this, report that the comparison could not be made rather
# than inventing a multiple. memory_used_bytes and filefd_allocated clear it by
# orders of magnitude on any real host; an idle UDP socket count does not.
MIN_BASELINE_FOR_RATIO = 32

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

# How many CONSECUTIVE failed samples make a failure "sustained".
#
# The criterion reads "no SUSTAINED Redis or health-check failures", not "no
# failures". A single missed sample during a container restart, a leader
# election or a rolling deploy is not an outage, and grading it as one makes the
# gate cry wolf until somebody stops reading it.
#
# Three at the 15-second sampling interval is roughly 45 seconds of continuous
# failure. That is chosen to sit above the things that legitimately blip and
# below anything a caller would tolerate: a Redis unreachable for 45 seconds has
# dropped calls, and a SIP node refusing health checks for 45 seconds is out of
# the load balancer's rotation. One sample would fire on noise; ten would be
# four minutes of a dead dependency reported as healthy.
#
# Consecutive, not cumulative, and the distinction is the whole point: thirty
# scattered single-sample failures across an hour are a flapping dependency
# worth a WARN, while three in a row are an outage.
MAX_CONSECUTIVE_FAILED_SAMPLES = 3

# The fewest samples a trend may be computed from.
#
# A slope over three points is noise wearing a trend's clothes. The steady-state
# comparison below splits a window into thirds and reads the last against the
# middle, so this is the floor for EACH third, not for the window: six samples
# total, ninety seconds at the sampling interval.
#
# Below it the gate is UNKNOWN, never PASS. "Too short to tell" and "flat" look
# identical in a single number and support opposite conclusions about a leak.
MIN_TREND_SAMPLES = 3

# The smallest drift that may be called a leak, as a fraction of the metric's
# OWN steady-state baseline.
#
# Without this the gate read the SIGN of ordinary memory noise. On a real
# 100-concurrent fleet run it failed monitor-0 on 407 KB of movement, 0.0057% of
# 7.15 GB, over ten minutes, on the box running the collector and carrying no
# test load at all, while sip-1 moved +45.8 MB the other way and passed. A false
# FAIL in a client deliverable is worse than the honest UNKNOWN it replaced.
#
# 1% is twenty times the largest noise observed on that run (0.05% of baseline)
# and three times the largest favourable-direction movement (0.3%). It passes
# all fourteen of its trend rows while leaving a real leak nowhere to hide: a
# leak that matters over ten minutes is far larger than 1% of baseline.
#
# A FRACTION of baseline rather than a rate per unit time, deliberately.
# Measurement noise scales with the size of the thing being measured, not with
# how long you watch it, so a rate threshold would divide a fixed noise floor by
# a short window and make short runs HYPERSENSITIVE, which is the exact failure
# being fixed here. A leak, by contrast, accumulates: watch longer and it climbs
# past this floor on its own. The per-hour rate is still reported in the detail,
# because that is the number a soak wants to read.
MIN_TREND_DRIFT_FRACTION = 0.01

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

# The resources the criterion names, one per HeadroomReading.resource.
#
# Network is TWO resources, not one, and the split is not cosmetic. A cloud
# instance meters inbound and outbound against SEPARATE credit buckets: a node
# can be shaped on egress while ingress sits nowhere near its allowance, and the
# fleet's own counters agree (bw_in and bw_out are distinct series that move
# independently). A single combined row would have to silently pick a direction,
# and whichever it picked would be the one nobody asked about.
#
# The two ids are also what keeps the ROWS distinct. One "network" resource
# emitted two gates whose subject was `node/network` with different answers and
# nothing to tell them apart, which is the same subject collision that was fixed
# once already by putting the source in the subject. Two resources, two
# subjects, two answers.
HEADROOM_FILE_DESCRIPTORS = "file_descriptors"
HEADROOM_RTP_PORTS = "rtp_ports"
HEADROOM_NETWORK_IN = "network_in"
HEADROOM_NETWORK_OUT = "network_out"

# Packets per second. A PERMANENT SCOPE EXCLUSION with a stated reason, not a
# gap waiting on an exporter, and the distinction is the whole point of writing
# it down here.
#
# Packets-per-second headroom cannot be expressed as a percentage by anyone,
# including AWS, because no per-instance-type PPS allowance is published under
# any API or document. There is a numerator (the observed packet rate) and there
# is no denominator to divide it by, anywhere, at any price. Every "PPS
# headroom" figure in existence is somebody's guess at the allowance wearing a
# percentage sign.
#
# So this must NEVER be turned into a headroom gate, and it is named here
# precisely so the next person to notice PPS is missing from the headroom rows
# finds the reason instead of filing it as unfinished work. Wiring an exporter
# does not lift this one: the counter that is missing is not a metric anybody
# collects, it is a constant only the hypervisor knows.
#
# PPS saturation is still DETECTED, which is why the exclusion is honest rather
# than a hole. node_ethtool_pps_allowance_exceeded feeds
# :data:`NETWORK_ALLOWANCE_GATE`, which reads the counter as an EVENT ("the
# instance was throttled during this window") rather than as a fraction of a
# limit. The event is measurable and gated; only the headroom is not.
HEADROOM_PPS = "pps"


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
    # Which step of a multi-step run produced this row. Part of the row's
    # IDENTITY, not of its judgement: nothing here changes what is emitted or
    # how anything grades.
    #
    # ``subject`` alone stopped being unique the moment the same resource was
    # gated once per step. A seven-step run rendered seven rows reading
    # ``sura-sip-01/node-exporter/file_descriptors`` with nothing to tell them
    # apart, and a reader cannot know whether that is one finding or seven. The
    # same collision was fixed once already for sources, by adding the source to
    # the subject; the step is the other half of the same identity.
    #
    # None for gates that are genuinely run-level rather than per-step, which is
    # the honest reading: stamping a step on a fleet-wide exclusion would claim
    # it was evaluated once per step when it was evaluated once.
    step: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """A JSON-safe copy (the run payload is persisted and served as JSON).

        ``waiver_reason`` is omitted entirely when it is None, so the payload of
        every gate that was not waived keeps exactly the shape it had before
        waivers existed. That shape is a contract: it is persisted with each run,
        served to the dashboard, and pinned by a test that asserts the key set.
        A waived gate is the only one that carries new information, so it is the
        only one whose payload grows.

        ``step`` follows the same rule for the same reason. A diagnostics gate
        belongs to no step and keeps the exact key set it has always had; only a
        row that really is one of several per run grows a key.
        """
        out = asdict(self)
        if out.get("waiver_reason") is None:
            out.pop("waiver_reason", None)
        if out.get("step") is None:
            out.pop("step", None)
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
            f"no rtt reading: {unmeasured}"
            if unmeasured is not None
            else f"rtt {rtt}ms"
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


def two_way_media_gate(
    *,
    answered_with_inbound: Any,
    answered_without_inbound: Any,
    subject: str | None = None,
) -> GateResult:
    """Did the calls that answered actually carry audio back?

    THE THRESHOLD IS ZERO, and it is not an invented bar. This project already
    defines a call that answered and carried no audio as a failure rather than a
    success, and says so in its own reports. This gate is that definition made
    enforceable. Every other threshold in this module was contracted for; this
    one is a definition, which is why it is not configurable.

    WHY A COUNT AND NOT THE PACKET RATIO. The run totals cannot answer this. A
    test where every call carries full audio and a test where half the calls
    carry none and half carry double report the same packets received. Observed
    on a 24 hour run: 100% establishment, zero failed calls, a 0.496 received
    per sent ratio, and 12,198 of 28,804 calls that had received nothing at all.
    The ratio was in the report the whole time and no gate read it, so the run
    was presented as clean.

    UNMEASURED IS NOT A PASS, following this module's rule everywhere else. If
    the per-call records were absent, unreadable, or of an unmapped schema the
    caller passes None and this returns UNKNOWN. A run whose media nobody
    counted has not demonstrated two-way media, and returning PASS for it would
    reintroduce exactly the blind spot this gate closes.

    Synchronous, like every gate in this module. Do not await it.
    """
    with_in = _as_count(answered_with_inbound)
    without_in = _as_count(answered_without_inbound)
    if with_in is None or without_in is None:
        return GateResult(
            gate=TWO_WAY_MEDIA_GATE,
            status=UNKNOWN,
            subject=subject,
            detail=(
                "per-call media was not measured: the run carried no readable "
                "call records, so whether each answered call received audio is "
                "unknown. Run totals cannot substitute, because they average a "
                "silent call against a loud one. Collect calls.jsonl from the "
                "generator to answer it"
            ),
            threshold=0,
        )
    total = with_in + without_in
    if total <= 0:
        return GateResult(
            gate=TWO_WAY_MEDIA_GATE,
            status=UNKNOWN,
            subject=subject,
            detail=(
                "the return path was not measured: no answered call reported "
                "sending any RTP, so there was no outbound stream whose reply "
                "could be checked"
            ),
            threshold=0,
        )
    status = PASS if without_in == 0 else FAIL
    if status == PASS:
        detail = f"all {total} answered calls received audio back from the fleet"
    else:
        detail = (
            f"{without_in} of {total} answered calls ({without_in / total * 100:.1f}%) "
            "sent audio and received NONE back. A call that answered and "
            "carried no audio is a failure, not a success"
        )
    return GateResult(
        gate=TWO_WAY_MEDIA_GATE,
        status=status,
        subject=subject,
        detail=detail,
        metric="calls_without_inbound_media",
        value=without_in,
        threshold=0,
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
                # Fleet-scoped, because the finding is that no node reported at
                # all. A blank subject left an empty cell beside five populated
                # ones and read as a rendering fault rather than a scope.
                subject=FLEET_SUBJECT,
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
    gauge pair. RTP ports come from ``media_ports_in_use`` /
    ``media_ports_total``, where the total is the DECLARED size of the
    configured media port range rather than a measurement, which is exactly why
    it is stored per sample: the ratio is taken against the range in force at
    that instant. Network is two resources, :data:`HEADROOM_NETWORK_IN` and
    :data:`HEADROOM_NETWORK_OUT`, because the buckets are separate.

    A pair this reading cannot be built from is not silently dropped:
    :func:`unscraped_headroom_readings` exists so an unmeasured resource still
    files a row saying so.
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
    """The headroom resources a caller could not measure, as explicit
    non-readings.

    The criterion asks for headroom on network, RTP ports and system limits.
    Emitting the ones a run could not compute as ``not_measured`` is the whole
    point of this helper: a report that simply omits them shows one green
    file-descriptor row and reads as full coverage of a three-part requirement.

    A helper rather than a note in a docstring, because a caller that has to
    remember to add them is a caller that eventually does not.

    Network appears TWICE, once per direction, for the reason given at
    :data:`HEADROOM_NETWORK_IN`: the credit buckets are separate, and a single
    row cannot be un-measured in one direction and measured in the other.

    :data:`HEADROOM_PPS` is deliberately NOT here. It is a stated permanent
    exclusion, not something awaiting a scrape, and filing it as an unmeasured
    headroom row would put it in the queue of things somebody could fix. Nobody
    can fix it: see the comment on that constant.
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
                "no scrape correlated to this window carried the "
                "media_ports_in_use / media_ports_total pair, so the range size "
                "that would be the denominator is unknown and nothing "
                "measures it",
            ),
            (
                HEADROOM_NETWORK_IN,
                "network_receive_bytes_total has no denominator here: no "
                "exporter in the scrape set publishes the instance INBOUND "
                "bandwidth allowance, so nothing measures it",
            ),
            (
                HEADROOM_NETWORK_OUT,
                "network_transmit_bytes_total has no denominator here: no "
                "exporter in the scrape set publishes the instance OUTBOUND "
                "bandwidth allowance, so nothing measures it",
            ),
        )
    ]


def _headroom_gate(reading: HeadroomReading, threshold: float) -> GateResult:
    # The source belongs in the subject whenever there is one, exactly as in
    # _resource_gate. One node is commonly scraped by several exporters, and
    # dropping it produced several gates with IDENTICAL subjects: an observed
    # run emitted "live-1/file_descriptors" three times reading PASS, UNKNOWN
    # and UNKNOWN, with nothing in the report saying which exporter passed. A
    # reading with no source keeps the shorter form, so a caller that has only
    # a node name is unaffected.
    where = f"{reading.node}/{reading.source}" if reading.source else reading.node
    subject = f"{where}/{reading.resource}"
    if reading.used is None or reading.limit is None:
        why = reading.unmeasured_reason or "the window produced no usable reading"
        return GateResult(
            gate=HEADROOM_GATE,
            status=UNKNOWN,
            subject=subject,
            detail=(
                f"{reading.resource} headroom on {where} was not "
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
                f"the {reading.resource} counts on {where} do not "
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
            f"{reading.resource} on {where} had "
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
                    # "not measured" is load-bearing wording, not prose. Every
                    # UNKNOWN row in a report has to say which kind of absence
                    # it is, and two end-to-end tests enforce that every UNKNOWN
                    # detail carries either "not measured" or "no node was
                    # sampled". The older phrasing said "Nothing measured" and
                    # satisfied neither, so this row read as a gate that broke
                    # rather than as a resource nobody supplied a reading for.
                    "no headroom reading was supplied, so this resource was not "
                    "measured and no limit was shown to have room left. Nothing "
                    "measured is not room to spare"
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
    if c.baseline < MIN_BASELINE_FOR_RATIO:
        return GateResult(
            gate=RETURN_TO_BASELINE_GATE,
            status=UNKNOWN,
            subject=subject,
            detail=(
                f"the {c.metric} baseline on {c.node} is {c.baseline:g}, below "
                f"{MIN_BASELINE_FOR_RATIO}, so a ratio against it is arithmetic "
                f"rather than evidence: it settled at {c.post_settle:g}, and at "
                "this baseline the smallest possible movement already exceeds "
                "the tolerance"
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


# --------------------------------------------------------------------------
# Sustained Redis and health-check failures
# --------------------------------------------------------------------------

#: What a health series is ABOUT, which is also how its row is labelled.
HEALTH_SUBJECT_REDIS = "redis"
HEALTH_SUBJECT_ENDPOINT = "health_endpoint"


@dataclass(frozen=True)
class HealthSeriesReading:
    """One source's healthy/failed samples across a window, in time order.

    ``samples`` carries 1 healthy, 0 failed and None NOT MEASURED, and the three
    are never collapsed. None is not a failure: a tick nobody sampled says
    nothing about whether the dependency was up, and counting it as a failure
    would manufacture an outage out of a scrape gap.

    ``codes`` is the HTTP status alongside each sample where there was one, so a
    row can say 503 rather than only "unhealthy". Empty for Redis, which has no
    status code.

    ``unmeasured_reason`` is set when the series could not be read at all, and
    it is what separates "not configured" from "configured and failing".
    """

    node: str
    subject: str
    source: str | None = None
    samples: tuple[int | None, ...] = ()
    codes: tuple[int | None, ...] = ()
    unmeasured_reason: str | None = None


def longest_failure_run(samples: Sequence[int | None]) -> int:
    """The longest run of CONSECUTIVE failed samples.

    A None breaks the run rather than extending it. Two failures either side of
    an unsampled tick are not demonstrably three consecutive failures, and this
    gate must not claim a continuity it did not observe.
    """
    longest = current = 0
    for value in samples:
        if value == 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _health_subject(reading: HealthSeriesReading) -> str:
    parts = [reading.node]
    if reading.source:
        parts.append(reading.source)
    parts.append(reading.subject)
    return "/".join(parts)


def sustained_health_gate(
    reading: HealthSeriesReading,
    *,
    threshold: int = MAX_CONSECUTIVE_FAILED_SAMPLES,
) -> GateResult:
    """Did this dependency fail for long enough to count?

    Three outcomes, and the middle one is why this is not a boolean.

    FAIL is a run of ``threshold`` consecutive failed samples: an outage.
    WARN is failures that never reached that run: a flapping dependency, which
    is a real finding and not a pass, but is not the criterion being breached.
    PASS is no failed sample at all.

    UNKNOWN when nothing was measured, with a reason that says whether the
    source was unconfigured or configured and unreadable. Those are different
    facts and an operator has to be able to tell them apart, so an unconfigured
    dependency NEVER reads as a pass.
    """
    subject = _health_subject(reading)
    if reading.unmeasured_reason:
        return GateResult(
            gate=SUSTAINED_HEALTH_GATE,
            status=UNKNOWN,
            detail=f"{subject} was not measured: {reading.unmeasured_reason}",
            subject=subject,
            threshold=float(threshold),
        )
    measured = [s for s in reading.samples if s is not None]
    if not measured:
        return GateResult(
            gate=SUSTAINED_HEALTH_GATE,
            status=UNKNOWN,
            detail=(
                f"{subject} was not measured: the window carried "
                f"{len(reading.samples)} sample(s) and none of them recorded a "
                "result"
            ),
            subject=subject,
            threshold=float(threshold),
        )

    run = longest_failure_run(reading.samples)
    failed = sum(1 for s in measured if s == 0)
    seen = _failure_codes(reading)
    codes = f" Statuses seen: {seen}." if seen else ""

    if run >= threshold:
        return GateResult(
            gate=SUSTAINED_HEALTH_GATE,
            status=FAIL,
            detail=(
                f"{subject} failed {run} consecutive sample(s), at or above the "
                f"{threshold}-sample bar for a sustained failure "
                f"({failed} of {len(measured)} measured samples failed).{codes}"
            ),
            subject=subject,
            metric="consecutive_failed_samples",
            value=float(run),
            threshold=float(threshold),
        )
    if failed:
        return GateResult(
            gate=SUSTAINED_HEALTH_GATE,
            status=WARN,
            detail=(
                f"{subject} failed {failed} of {len(measured)} measured "
                f"sample(s), longest run {run}, below the {threshold}-sample bar "
                f"for a sustained failure. Not the criterion breached, but not "
                f"a clean window either.{codes}"
            ),
            subject=subject,
            metric="consecutive_failed_samples",
            value=float(run),
            threshold=float(threshold),
        )
    return GateResult(
        gate=SUSTAINED_HEALTH_GATE,
        status=PASS,
        detail=(f"{subject} was healthy on all {len(measured)} measured sample(s)."),
        subject=subject,
        metric="consecutive_failed_samples",
        value=0.0,
        threshold=float(threshold),
    )


def _failure_codes(reading: HealthSeriesReading) -> str:
    """The distinct HTTP statuses seen on FAILED samples, with their counts.

    Ordered by the string form of the status, which is stable rather than
    meaningful: it exists so one run's row reads the same as the next, not
    because 429 matters more than 503.

    429 UnderLoad and 503 Unavailable are different problems and the row must
    say which. A failure with no code at all was a refusal or a timeout, which
    is a third different problem and is named rather than left blank.
    """
    if not reading.codes:
        return ""
    seen: dict[str, int] = {}
    for sample, code in zip(reading.samples, reading.codes, strict=False):
        if sample != 0:
            continue
        key = str(code) if code is not None else "no response"
        seen[key] = seen.get(key, 0) + 1
    if not seen:
        return ""
    return ", ".join(f"{k} x{v}" for k, v in sorted(seen.items()))


def sustained_health_gates(
    readings: Sequence[HealthSeriesReading],
    *,
    threshold: int = MAX_CONSECUTIVE_FAILED_SAMPLES,
) -> list[GateResult]:
    """One gate per node per source. Per-node rows are never collapsed."""
    return [sustained_health_gate(r, threshold=threshold) for r in readings]


# --------------------------------------------------------------------------
# Unplanned restarts, crashes and OOM kills
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LifecycleReading:
    """One subject's process-start times and OOM counter across a window.

    ``start_times`` is ``process_start_time_seconds`` in time order. It is a
    CONSTANT for the life of a process, so any increase inside a window means
    that process restarted mid-run, and a crash presents exactly that way.

    ``oom_kills`` is ``node_vmstat_oom_kill``, cumulative since boot, so an
    increase inside the window means the kernel killed something DURING the run.
    A box OOM-killed last week is not this run's finding.

    None entries are gaps, never zero, and a subject with nothing but gaps is
    UNKNOWN rather than a pass.
    """

    node: str
    source: str | None = None
    start_times: tuple[float | None, ...] = ()
    oom_kills: tuple[float | None, ...] = ()


def _rose(values: Sequence[float | None]) -> tuple[bool, float | None, float | None]:
    """Whether a series increased, and the pair that shows it. Gaps skipped."""
    seen = [v for v in values if v is not None]
    if len(seen) < 2:
        return False, (seen[0] if seen else None), None
    lo, hi = seen[0], seen[0]
    for value in seen:
        if value > hi:
            hi = value
        if value < lo:
            lo = value
    first = seen[0]
    for value in seen[1:]:
        if value > first:
            return True, first, hi
    return False, lo, hi


def process_lifecycle_gate(reading: LifecycleReading) -> GateResult:
    """Did anything restart, crash or get OOM-killed during this window?

    Restarts and OOM are read together here because they answer one contracted
    line, but they are NEVER collapsed into one signal: the detail says which
    fired, and an unmeasured half is reported as unmeasured rather than covered
    by the measured half. "No restart" does not prove "no OOM".
    """
    subject = f"{reading.node}/{reading.source}" if reading.source else reading.node
    restarted, first_start, last_start = _rose(reading.start_times)
    oomed, _, oom_high = _rose(reading.oom_kills)
    measured_starts = [v for v in reading.start_times if v is not None]
    measured_ooms = [v for v in reading.oom_kills if v is not None]

    if restarted:
        return GateResult(
            gate=PROCESS_LIFECYCLE_GATE,
            status=FAIL,
            subject=subject,
            detail=(
                f"{subject} restarted during the window: "
                f"process_start_time_seconds rose from {first_start} to "
                f"{last_start}. A crash presents this way too, so this covers "
                "both, and every counter rate across the restart is unusable."
            ),
            metric="process_restarts",
            value=1.0,
            threshold=0.0,
        )
    if oomed:
        return GateResult(
            gate=PROCESS_LIFECYCLE_GATE,
            status=FAIL,
            subject=subject,
            detail=(
                f"the kernel OOM-killed at least one process on {subject} "
                f"during the window (node_vmstat_oom_kill reached {oom_high}). "
                "The scraped service did not restart, so this would not have "
                "shown up as a restart."
            ),
            metric="oom_kills",
            value=1.0,
            threshold=0.0,
        )
    if not measured_starts and not measured_ooms:
        return GateResult(
            gate=PROCESS_LIFECYCLE_GATE,
            status=UNKNOWN,
            subject=subject,
            detail=(
                f"{subject} was not measured: it carried neither "
                "process_start_time_seconds nor node_vmstat_oom_kill in the "
                "window, so neither restarts nor OOM kills were checked. "
                "Nothing was shown to be stable"
            ),
            threshold=0.0,
        )
    if not measured_starts:
        return GateResult(
            gate=PROCESS_LIFECYCLE_GATE,
            status=UNKNOWN,
            subject=subject,
            detail=(
                f"{subject} was not measured for restarts: it recorded no OOM "
                "kill but carried no process_start_time_seconds, so a restart "
                "could not be ruled out. No OOM does not prove no restart"
            ),
            threshold=0.0,
        )
    if not measured_ooms:
        return GateResult(
            gate=PROCESS_LIFECYCLE_GATE,
            status=UNKNOWN,
            subject=subject,
            detail=(
                f"{subject} was not measured for OOM: it did not restart across "
                f"{len(measured_starts)} sample(s) but carried no "
                "node_vmstat_oom_kill, so an OOM kill could not be ruled out. "
                "No restart does not prove no OOM"
            ),
            threshold=0.0,
        )
    return GateResult(
        gate=PROCESS_LIFECYCLE_GATE,
        status=PASS,
        subject=subject,
        detail=(
            f"{subject} held one process start time across "
            f"{len(measured_starts)} sample(s) and recorded no OOM kill across "
            f"{len(measured_ooms)}."
        ),
        metric="process_restarts",
        value=0.0,
        threshold=0.0,
    )


def process_lifecycle_gates(
    readings: Sequence[LifecycleReading],
) -> list[GateResult]:
    """One row per node per source. Never collapsed."""
    return [process_lifecycle_gate(r) for r in readings]


# --------------------------------------------------------------------------
# Stale resources: a trend that should be flat
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TrendReading:
    """One subject's series for one metric, in time order, over a window.

    ``rising_is_bad`` says which direction is the leak. Occupancy metrics such
    as ``sip_calls_active`` leak upward; ``memory_available_bytes`` leaks
    DOWNWARD, because free memory falling is the same event as used memory
    rising. Getting that backwards would report a leak as healthy.
    """

    node: str
    metric: str
    source: str | None = None
    values: tuple[float | None, ...] = ()
    rising_is_bad: bool = True
    #: How long the window spanned, so the detail can quote a rate. Absent just
    #: means the rate is not reported; it never changes the verdict.
    window_ms: int | None = None


def steady_state_thirds(
    values: Sequence[float | None],
) -> tuple[list[float], list[float]]:
    """The middle and final thirds of a window, gaps removed.

    Middle against final, NEVER first against last. Under a fixed offered load
    the first third is the ramp, and a ramp read as a trend reports every
    successful run as a leak.
    """
    size = len(values) // 3
    if size == 0:
        return [], []
    middle = [v for v in values[size : size * 2] if v is not None]
    final = [v for v in values[size * 2 :] if v is not None]
    return middle, final


def _drift_rate_per_hour(drift: float, window_ms: int | None) -> str:
    """The drift as a per-hour rate, or empty when the window is unknown.

    Reported, never gated on. A ten-minute run and a twenty-four-hour soak are
    not comparable by absolute drift, and this is the number that makes them
    comparable to a reader without making the THRESHOLD depend on how long
    somebody happened to watch.
    """
    if not window_ms or window_ms <= 0:
        return ""
    per_hour = drift * 3_600_000.0 / window_ms
    return f" That is {per_hour:+.4g} per hour."


def resource_trend_gate(
    reading: TrendReading,
    *,
    min_samples: int = MIN_TREND_SAMPLES,
    min_drift_fraction: float = MIN_TREND_DRIFT_FRACTION,
) -> GateResult:
    """Did a resource that should be flat in steady state drift instead?

    UNKNOWN below ``min_samples`` in either third. A slope over two points is
    noise, and "too short to tell" must never render as "flat".
    """
    subject = (
        f"{reading.node}/{reading.source}/{reading.metric}"
        if reading.source
        else f"{reading.node}/{reading.metric}"
    )
    middle, final = steady_state_thirds(reading.values)
    if len(middle) < min_samples or len(final) < min_samples:
        return GateResult(
            gate=RESOURCE_TREND_GATE,
            status=UNKNOWN,
            subject=subject,
            detail=(
                f"{reading.metric} on {subject} was not measured as a trend: the "
                f"steady-state thirds carried {len(middle)} and {len(final)} "
                f"usable sample(s), below the {min_samples} each needs. Too "
                "short to tell is not flat"
            ),
            threshold=0.0,
        )
    before = sum(middle) / len(middle)
    after = sum(final) / len(final)
    drift = after - before
    wrong_way = drift > 0 if reading.rising_is_bad else drift < 0
    direction = "rose" if drift > 0 else "fell"
    rate = _drift_rate_per_hour(drift, reading.window_ms)
    # Relative to the metric's OWN baseline, so one floor serves bytes and
    # counts alike. A zero baseline has no fraction to take, and there any
    # movement of a whole unit is real: a count going 0 to 5 in steady state is
    # not noise.
    if before:
        share = abs(drift) / abs(before)
        floor_detail = (
            f"{share:.4%} of its {before:.4g} baseline, against a "
            f"{min_drift_fraction:.0%} floor"
        )
        big_enough = share >= min_drift_fraction
    else:
        floor_detail = "measured against a zero baseline, so any whole unit counts"
        big_enough = abs(drift) >= 1.0
    if wrong_way and big_enough:
        return GateResult(
            gate=RESOURCE_TREND_GATE,
            status=FAIL,
            subject=subject,
            detail=(
                f"{reading.metric} on {subject} {direction} across steady "
                f"state: mean {before:.4g} over the middle third against "
                f"{after:.4g} over the final third, {floor_detail}. Under a "
                f"fixed offered load this should be flat, so the drift is the "
                f"leak.{rate}"
            ),
            metric=reading.metric,
            value=float(drift),
            threshold=float(min_drift_fraction),
        )
    if wrong_way:
        return GateResult(
            gate=RESOURCE_TREND_GATE,
            status=PASS,
            subject=subject,
            detail=(
                f"{reading.metric} on {subject} {direction} slightly across "
                f"steady state, by {floor_detail}. That is measurement noise "
                f"rather than a leak: the measurement succeeded and found no "
                f"meaningful drift.{rate}"
            ),
            metric=reading.metric,
            value=float(drift),
            threshold=float(min_drift_fraction),
        )
    return GateResult(
        gate=RESOURCE_TREND_GATE,
        status=PASS,
        subject=subject,
        detail=(
            f"{reading.metric} on {subject} held flat or improved across steady "
            f"state: mean {before:.4g} over the middle third against "
            f"{after:.4g} over the final third.{rate}"
        ),
        metric=reading.metric,
        value=float(drift),
        threshold=float(min_drift_fraction),
    )


def resource_trend_gates(
    readings: Sequence[TrendReading],
    *,
    min_samples: int = MIN_TREND_SAMPLES,
    min_drift_fraction: float = MIN_TREND_DRIFT_FRACTION,
) -> list[GateResult]:
    return [
        resource_trend_gate(
            r, min_samples=min_samples, min_drift_fraction=min_drift_fraction
        )
        for r in readings
    ]


# --------------------------------------------------------------------------
# Hypervisor network allowances: throttling no application metric explains
# --------------------------------------------------------------------------
#
# A cloud host does not drop your traffic when you exceed an allowance, it
# QUEUES it, and the queue is invisible from inside the guest. It presents to a
# caller as jitter and one-way audio that CPU, memory and application latency
# all decline to explain. The ethtool allowance counters are the only place the
# hypervisor admits it happened, which is what makes them worth a gate of their
# own rather than a footnote under headroom.
#
# This gate answers "were you throttled", never "how close were you". The
# second question is the headroom gate's, and for three of these five counters
# it has no answer at all: see :data:`HEADROOM_PPS`.


#: The five counters, in the order a row prints them, split by WHAT A NON-ZERO
#: READING PROVES. The split is the reason this gate exists, so it is data here
#: rather than a branch inside the gate: the sentence a reader gets has to
#: follow from which counter fired.
#:
#: bw_in, bw_out and pps count packets QUEUED OR DROPPED, and the hypervisor
#: never separates the two. A delta there is proof of SHAPING and is not proof
#: of loss, and a report that says "packets dropped" off this counter has
#: claimed something the counter cannot tell it.
_ALLOWANCE_SHAPED: tuple[str, ...] = ("bw_in", "bw_out", "pps")
#: conntrack and linklocal count DROPS ONLY. There is no queue behind these: a
#: non-zero delta is traffic that did not arrive, which is an unambiguous
#: failure and has to read differently from the three above.
_ALLOWANCE_DROPPED: tuple[str, ...] = ("conntrack", "linklocal")
_ALLOWANCE_COUNTERS: tuple[str, ...] = _ALLOWANCE_SHAPED + _ALLOWANCE_DROPPED


@dataclass(frozen=True)
class AllowanceReading:
    """One node's five hypervisor allowance counters as DELTAS over the window.

    **Deltas, never absolutes, and the gate depends on the caller honouring
    that.** ``node_ethtool_*_allowance_exceeded`` is cumulative since driver
    reset, so an absolute reading carries every throttling event since the
    instance booted. This is not theoretical: one real SIP node reads 9613 on
    ``bw_in`` from before the engagement started while its twin, same instance
    type behind the same load balancer, reads 0. Gating the absolute would fail
    a node for shaping it never suffered during the test and pass its twin, and
    the report would name the wrong box.

    The caller supplies the subtraction because the caller is the one holding
    both endpoints of the window. This gate therefore treats a large value as a
    large delta and nothing else, and a NEGATIVE value as a counter reset rather
    than as traffic being handed back.

    ``None`` means that counter was not measured. It is never read as zero: a
    counter nobody scraped says nothing about whether the instance was
    throttled, and scoring it 0 would manufacture a clean window out of a scrape
    gap.
    """

    node: str
    source: str | None = None
    # delta over the window per counter; None means not measured
    bw_in: float | None = None
    bw_out: float | None = None
    pps: float | None = None
    conntrack: float | None = None
    linklocal: float | None = None


def _allowance_subject(reading: AllowanceReading) -> str:
    """``node`` or ``node/source``, and never blank.

    Same rule as :func:`_headroom_gate`: one node is commonly scraped by several
    exporters, and dropping the source produced rows with identical subjects and
    different answers. The fleet fallback covers a reading built with no node
    name at all, because an empty cell in a table where every other row
    identifies itself reads as a rendering fault rather than as a finding.
    """
    node = reading.node.strip() if reading.node else ""
    if not node:
        return FLEET_SUBJECT
    return f"{node}/{reading.source}" if reading.source else node


def _allowance_names(names: Sequence[str]) -> str:
    """``a``, ``a and b``, ``a, b and c``. Prose, because the detail is prose."""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def network_allowance_gate(reading: AllowanceReading) -> GateResult:
    """Did the hypervisor throttle this node during the window?

    PASS is every measured counter at a delta of zero. FAIL is any measured
    counter above zero. UNKNOWN is no counter measured at all, which is not a
    pass: an instance nobody asked about its allowances is not an instance that
    stayed inside them.

    **The detail is the deliverable here, more than the status is.** A bare
    "FAIL, 9613" invites exactly one wrong reading, that 9613 packets were lost,
    and the number does not support it. Two facts change what the number means
    and both are stated on every row that carries one:

    * ``bw_in``, ``bw_out`` and ``pps`` count packets QUEUED OR DROPPED, with no
      way to tell which. Non-zero is evidence of shaping. It is not evidence of
      loss, and a client who is told it is will go looking for a fault that
      leaves no other trace.
    * ``conntrack`` and ``linklocal`` count DROPS ONLY. Non-zero there is
      traffic that did not arrive, full stop, and it must not be softened into
      the same sentence as the first three.

    A partial window still PASSES on what it measured and SAYS what it did not,
    because "three of five counters were clean" and "the node was clean" are
    different claims and only the first one was earned.
    """
    subject = _allowance_subject(reading)
    values = {name: getattr(reading, name) for name in _ALLOWANCE_COUNTERS}

    fired = [(n, v) for n, v in values.items() if v is not None and v > 0]
    clean = [n for n, v in values.items() if v is not None and v == 0]
    # A delta below zero is not traffic being credited back, it is the counter
    # having reset under the window (driver reload, live migration, an instance
    # replaced mid-run). The window's true delta is then unknowable, so the
    # counter is dropped from the evidence rather than scored as clean.
    reset = [n for n, v in values.items() if v is not None and v < 0]
    unmeasured = [n for n, v in values.items() if v is None]

    if fired:
        named = ", ".join(f"{n} {v:+g}" for n, v in fired)
        shaped = [n for n, _ in fired if n in _ALLOWANCE_SHAPED]
        dropped = [n for n, _ in fired if n in _ALLOWANCE_DROPPED]
        clauses = []
        if shaped:
            verb = "counts" if len(shaped) == 1 else "count"
            clauses.append(
                f"{_allowance_names(shaped)} {verb} packets QUEUED OR DROPPED "
                "and the hypervisor never separates the two, so a non-zero "
                "delta there is evidence of shaping, not necessarily loss."
            )
        if dropped:
            verb = "counts" if len(dropped) == 1 else "count"
            clauses.append(
                f"{_allowance_names(dropped)} {verb} outright drops, so a "
                "non-zero delta there is traffic that did not arrive rather "
                "than traffic that was delayed."
            )
        return GateResult(
            gate=NETWORK_ALLOWANCE_GATE,
            status=FAIL,
            subject=subject,
            detail=(
                f"{subject} exceeded a hypervisor network allowance during the "
                f"window: {named} (delta over the window, not an absolute). "
                + " ".join(clauses)
            ),
            metric="allowance_events",
            value=float(sum(v for _, v in fired)),
            threshold=0.0,
        )

    if clean:
        gaps = ""
        if unmeasured:
            gaps = (
                f" Not measured: {_allowance_names(unmeasured)}, so those were "
                "not ruled out."
            )
        resets = ""
        if reset:
            resets = (
                f" Excluded as counter resets: {_allowance_names(reset)}, which "
                "fell over the window and cannot yield a delta."
            )
        return GateResult(
            gate=NETWORK_ALLOWANCE_GATE,
            status=PASS,
            subject=subject,
            detail=(
                f"{subject} recorded no hypervisor allowance event during the "
                f"window: {_allowance_names(clean)} held at a delta of 0."
                f"{gaps}{resets}"
            ),
            metric="allowance_events",
            value=0.0,
            threshold=0.0,
        )

    if reset:
        return GateResult(
            gate=NETWORK_ALLOWANCE_GATE,
            status=UNKNOWN,
            subject=subject,
            detail=(
                f"{subject} was not measured: every counter that reported "
                f"({_allowance_names(reset)}) fell across the window, which "
                "means the counter reset rather than that traffic was allowed, "
                "so the window's real delta is unknowable. A reset counter is "
                "not a quiet network"
            ),
            threshold=0.0,
        )

    return GateResult(
        gate=NETWORK_ALLOWANCE_GATE,
        status=UNKNOWN,
        subject=subject,
        detail=(
            f"{subject} was not measured: none of the five ethtool allowance "
            f"counters ({_allowance_names(_ALLOWANCE_COUNTERS)}) carried a "
            "delta for this window, so neither shaping nor drops were ruled "
            "out. A counter nobody read is not a quiet network"
        ),
        threshold=0.0,
    )


def network_allowance_gates(
    readings: Sequence[AllowanceReading],
) -> list[GateResult]:
    """One row per node per source. Never collapsed.

    Collapsing would lose the finding. On a real fleet one SIP node carries
    a non-zero ``bw_in`` and its twin carries zero, and which box was shaped is
    the entire actionable content of the row.
    """
    if not readings:
        return [
            GateResult(
                gate=NETWORK_ALLOWANCE_GATE,
                status=UNKNOWN,
                # Fleet-scoped for the same reason as _resource_gates: the
                # finding is that nothing reported, so naming a node would be
                # wrong and naming nothing prints an empty cell.
                subject=FLEET_SUBJECT,
                detail=(
                    "no node was sampled in the window, so no hypervisor "
                    "allowance counter was read and no node was shown to have "
                    "escaped throttling. A window with no samples is not a "
                    "quiet fleet"
                ),
                threshold=0.0,
            )
        ]
    return [network_allowance_gate(r) for r in readings]


__all__ = [
    "AGENTS_GATE",
    "BASELINE_GOROUTINES",
    "BASELINE_HEAP",
    "ESTABLISHMENT_GATE",
    "FLEET_SUBJECT",
    "FAIL",
    "HEADROOM_FILE_DESCRIPTORS",
    "HEADROOM_GATE",
    "HEADROOM_NETWORK_IN",
    "HEADROOM_NETWORK_OUT",
    "HEADROOM_PPS",
    "HEADROOM_RTP_PORTS",
    "LATENCY_GATE",
    "MAX_NODE_CPU_UTILISATION",
    "MAX_NODE_MEMORY_UTILISATION",
    "MIN_ESTABLISHMENT_RATIO",
    "MIN_HEADROOM_FRACTION",
    "ALL_GATES",
    "MIN_PERCENTILE_SAMPLES",
    "MIN_BASELINE_FOR_RATIO",
    "MIN_SETTLE_MS",
    "NODE_CPU_GATE",
    "NODE_MEMORY_GATE",
    "RATIO_GATES",
    "RETURN_TO_BASELINE_GATE",
    "SUSTAINED_HEALTH_GATE",
    "PROCESS_LIFECYCLE_GATE",
    "RESOURCE_TREND_GATE",
    "NETWORK_ALLOWANCE_GATE",
    "AllowanceReading",
    "network_allowance_gate",
    "network_allowance_gates",
    "MIN_TREND_SAMPLES",
    "MIN_TREND_DRIFT_FRACTION",
    "LifecycleReading",
    "TrendReading",
    "process_lifecycle_gate",
    "process_lifecycle_gates",
    "resource_trend_gate",
    "resource_trend_gates",
    "steady_state_thirds",
    "HEALTH_SUBJECT_REDIS",
    "HEALTH_SUBJECT_ENDPOINT",
    "HealthSeriesReading",
    "sustained_health_gate",
    "sustained_health_gates",
    "longest_failure_run",
    "MAX_CONSECUTIVE_FAILED_SAMPLES",
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
