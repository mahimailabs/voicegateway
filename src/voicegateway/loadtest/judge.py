"""Turn one load run's measurements into gate verdicts.

The gates existed and nothing called them. :mod:`voicegateway.livekit_diag.gates`
implements every contracted threshold correctly, and
:mod:`voicegateway.loadtest.aggregation` builds the readings they judge, but no
code path connected the two: a load-test report could carry numbers and no
verdict. A capacity table without a pass/fail against the acceptance criteria is
a table, not evidence.

This module is the wire. It measures nothing and decides nothing; it hands what
was measured to the gates and returns what they said.

Unmeasured is judged, not skipped
---------------------------------

A criterion nobody measured produces an UNKNOWN gate rather than no gate. That
distinction is the whole point. A report that silently omits the headroom gate
reads as a run with nothing to say about headroom; a report carrying
``headroom/rtp_ports: UNKNOWN`` says nobody looked, which is the true and much
more useful claim. So the resources nothing scrapes are emitted every time,
already UNKNOWN, rather than being left out.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from voicegateway.livekit_diag import gates
from voicegateway.livekit_diag.gates import GateResult
from voicegateway.loadtest.aggregation import (
    BASELINE_METRICS,
    FD_LIMIT_COLUMN,
    FD_USED_COLUMN,
    MEDIA_PORTS_TOTAL_COLUMN,
    MEDIA_PORTS_USED_COLUMN,
    TestAggregate,
)
from voicegateway.middleware.node_samples_worker_middleware import (
    any_source_publishes,
    reports_host_metrics,
    reports_process_metrics,
)

# The FD pair is the one headroom resource anything scrapes. RTP ports and
# network are emitted as unmeasured by unscraped_headroom_readings, so they
# appear in every report as UNKNOWN instead of vanishing.
# The PER-PROCESS rlimit pair, not the host filefd pair. The host maximum is
# commonly unbounded and yields no ratio at all; the limit a service actually
# hits is its own.
_FD_USED = FD_USED_COLUMN
_FD_LIMIT = FD_LIMIT_COLUMN


_NO_WINDOW = (
    "the test has no window, so no scrape carrying "
    f"{_FD_USED}/{_FD_LIMIT} could be correlated to it"
)
_NOT_ON_AGGREGATE = (
    f"the correlated window carried no {_FD_USED}/{_FD_LIMIT} reading for this node"
)
# The test HAS a window; nothing was scraped inside it. Distinct from _NO_WINDOW,
# and the distinction is visible to a client: the CPU and memory gates on the
# same run say "no node was sampled in the window", so reusing the no-window
# wording here made one report explain one absence two incompatible ways.
_NO_SAMPLES = (
    "no node was sampled in the window, so no scrape carrying "
    f"{_FD_USED}/{_FD_LIMIT} could be correlated to it"
)


def _fd_reading(
    node: str, source: str | None = None, *, reason: str = _NO_WINDOW
) -> gates.HeadroomReading:
    """File descriptors as an unmeasured reading, with the reason it is one.

    A test that correlated a window carries REAL readings on its aggregate,
    built where the measurement happens rather than rebuilt here: this module
    decides and does not measure. This is the fallback for the two ways a node
    can arrive with nothing to judge, and it exists so those cases produce an
    UNKNOWN gate rather than no gate at all.
    """
    return gates.HeadroomReading(
        node=node,
        resource="file_descriptors",
        used=None,
        limit=None,
        source=source,
        unmeasured_reason=reason,
    )


#: What each headroom resource would be COMPUTED FROM. The criterion asks for
#: headroom on network, RTP ports and system limits, and this says what each of
#: those three needs before it can be answered.
#:
#: The names for RTP ports and network are the columns that WOULD carry them. No
#: source publishes either pair: ``sockstat_udp_inuse`` is collected but the size
#: of the configured media port range is not, so there is a numerator and no
#: denominator, and nothing collects interface saturation at all.
#:
#: Declared here rather than as a list of excluded names, which is the whole
#: point. A resource is excluded because nothing can measure it, not because
#: somebody wrote it down, so wiring a source for these columns lifts the
#: exclusion by itself and the resource returns to being a real per-node gate.
HEADROOM_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    gates.HEADROOM_FILE_DESCRIPTORS: (FD_USED_COLUMN, FD_LIMIT_COLUMN),
    # A real measurement now, and it replaces the sockstat_udp_inuse proxy
    # entirely. That counted every UDP socket in the network namespace,
    # including the SFU's own ICE range, so it was an upper bound rather than a
    # count of media ports. media_ports_total is a DECLARED range size, which is
    # why a node publishing occupancy without it reaches the gate as UNKNOWN.
    gates.HEADROOM_RTP_PORTS: (
        MEDIA_PORTS_USED_COLUMN,
        MEDIA_PORTS_TOTAL_COLUMN,
    ),
    # Only the NUMERATOR is a column. There is no measurable link capacity: the
    # ENA driver reports no speed, so node_network_speed_bytes yields nothing
    # for the real interface. The denominator is the instance type's published
    # baseline, declared by an operator at import, so it can never appear here.
    # A node with throughput and no declaration is UNKNOWN at the gate, which is
    # a different and more useful answer than the whole resource being excluded.
    gates.HEADROOM_NETWORK_IN: ("network_receive_bytes_total",),
    gates.HEADROOM_NETWORK_OUT: ("network_transmit_bytes_total",),
}

#: Resources excluded for a reason no exporter can ever lift.
#:
#: Distinct from :data:`HEADROOM_REQUIREMENTS`, whose exclusions are DERIVED
#: from whether a column is published and therefore self-healing. This one is
#: not waiting on work: the denominator does not exist anywhere to be collected.
PERMANENT_HEADROOM_EXCLUSIONS: dict[str, str] = {
    gates.HEADROOM_PPS: (
        "packets-per-second headroom cannot be expressed as a percentage by "
        "anyone, including AWS: no per-instance-type PPS allowance is published "
        "under any API or document, so there is no denominator to divide by. "
        "This is not a gap awaiting work. PPS saturation is still DETECTED as an "
        "event by the network_allowance gate, which reads the "
        "pps_allowance_exceeded counter; what cannot be quantified is how much "
        "headroom remained."
    ),
}


def excluded_headroom_resources() -> dict[str, str]:
    """Headroom resources nothing in this system can measure, and why.

    A scope EXCLUSION rather than a gate. Grading these per node per test told a
    reader the same two facts eighteen times on a real run, above the table they
    contracted for, while a gate row implies a measurement was attempted on that
    node and failed. Nothing was attempted, and nothing could be.

    Stating it once does NOT reduce what the report discloses, and the report is
    responsible for making that true: an excluded resource must be at least as
    visible as eighteen rows were, because removing them can move the verdict
    off UNKNOWN and a headline that improves while the disclosure shrinks is the
    one outcome this must not produce.
    """
    excluded: dict[str, str] = dict(PERMANENT_HEADROOM_EXCLUSIONS)
    for resource, columns in HEADROOM_REQUIREMENTS.items():
        if any_source_publishes(*columns):
            continue
        missing = [c for c in columns if not any_source_publishes(c)]
        excluded[resource] = (
            f"no exporter in the scrape set publishes {' or '.join(missing)}, so "
            f"{resource.replace('_', ' ')} headroom is outside what this system "
            "measures on any node, in any run. It is not evaluated rather than "
            "passing, and the acceptance criterion it belongs to is not covered."
        )
    return excluded


def _reports_node_metrics(source: str | None) -> bool:
    """Whether to grade NODE-WIDE CPU and memory for this source.

    The one suppression this module makes, and it is deliberately the only one.

    A service exporter publishes nothing about the box it runs on. Verified
    against a live livekit-sip: zero node-wide series. Grading it UNKNOWN
    reports a failed measurement where none was attempted or possible, and a
    real run spent twelve of its forty-eight gate rows saying so, above the
    three-row table the client contracted for.

    Nothing else is suppressed. File descriptors in particular are NOT, because
    every source is a process with file handles: node_exporter publishes its own
    (a live one reads 9) even though this system does not wire them, so an
    absent FD reading there is a metric that COULD have been produced and was
    not. That is the signal the UNKNOWN status exists to raise.
    """
    return reports_host_metrics(source) is not False


def _speaks_for_process_fds(source: str | None) -> bool:
    """Whether a process-descriptor gate for this source means anything.

    Narrow on purpose. Only a source that is DEFINITELY not an authority is
    dropped, which today is the Redis exporter and nothing else.

    node_exporter is deliberately NOT dropped. It publishes the pair, it is now
    wired for it, and an absent reading there is a real gap rather than a
    category error. That decision is pinned by
    ``test_gate_only_where_measurable.py::test_node_exporter_keeps_its_file_descriptor_gate``
    and nothing here disturbs it: reports_process_metrics answers None for a
    host exporter, and only an explicit False suppresses.
    """
    return reports_process_metrics(source) is not False


def _graded(readings, gate_fn):
    """Grade the readings whose SOURCE can produce this metric.

    Two empty cases that must not be collapsed, which is why this is a function
    rather than a list comprehension at each call site.

    Nothing was scraped AT ALL: the gate functions report that as a single
    UNKNOWN, and it is a real finding. The window produced no samples and the
    run demonstrated no ceiling.

    Something WAS scraped and none of it came from a source that publishes this
    metric: there is nothing to grade, so no gate. Passing an empty list to the
    gate function here would resurrect the row this node exists to remove, and
    would say the fleet went unsampled when it did not.
    """
    if not readings:
        return list(gate_fn(readings))
    kept = [
        r
        for r in readings
        # A real number is graded whatever its source: the reading is evidence,
        # and dropping a measurement somebody took is worse than any tidying.
        if r.utilisation is not None or _reports_node_metrics(r.source)
    ]
    if not kept:
        return []
    return list(gate_fn(kept))


def judge_test(
    test: dict[str, Any],
    *,
    aggregate: TestAggregate | None = None,
    node: str | None = None,
    network_baselines: dict[str, dict[str, float]] | None = None,
) -> list[GateResult]:
    """Every acceptance gate for one test, in report order.

    ``test`` is a ``load_run_tests`` row as the repository serves it. ``aggregate``
    is what :func:`aggregation.aggregate_test_window` correlated to this test's
    window, or None when the test had no window to correlate against.

    Nothing here substitutes a value. A row whose counts were never imported
    yields UNKNOWN from the establishment gate rather than being dropped, and a
    window nobody scraped yields UNKNOWN from the resource gates rather than a
    clean sheet.
    """
    subject = str(test.get("name") or "test")
    results: list[GateResult] = [
        gates.establishment_gate(
            attempted=test.get("attempted_calls"),
            succeeded=test.get("succeeded_calls"),
            threshold=gates.MIN_ESTABLISHMENT_RATIO,
            subject=subject,
        ),
        # Establishment and two-way media are SEPARATE criteria and both are
        # emitted for every test. A call that answered and then carried no audio
        # passes the first and fails the second, and a run reporting only the
        # first calls that success. That is not hypothetical: a 24 hour run
        # scored 100% establishment with zero failures while 42% of its calls
        # received nothing.
        gates.two_way_media_gate(
            answered_with_inbound=test.get("calls_answered_with_inbound"),
            answered_without_inbound=test.get("calls_answered_without_inbound"),
            subject=subject,
        ),
    ]

    if aggregate is None:
        # No window, so nothing could be correlated. Say so once per resource
        # rather than emitting nothing: an absent gate reads as a criterion
        # nobody cared about.
        reason = (
            "the test has no recorded start and end, so no scrape window could "
            "be correlated to it"
        )
        unmeasured = gates.NodeUtilisationReading(
            node=node or subject, utilisation=None, unmeasured_reason=reason
        )
        results.extend(gates.node_cpu_gates([unmeasured]))
        results.extend(gates.node_memory_gates([unmeasured]))
        target = node or subject
        results.extend(gates.headroom_gates([_fd_reading(target)]))
        # The three lifecycle criteria are NOT emitted per test here. They are
        # run-level facts, reported once by unevaluated_criteria_gates, which is
        # what stops a seven-step run repeating the same absence seven times.
        return results

    # Filtered by what the SOURCE can publish, not by what this run produced.
    # Per-node reporting is untouched: at 500 concurrent across six SIP nodes,
    # knowing WHICH node breached is the point, so nothing here collapses rows.
    results.extend(_graded(aggregate.cpu_readings, gates.node_cpu_gates))
    results.extend(_graded(aggregate.memory_readings, gates.node_memory_gates))
    results.extend(_headroom_gates_for(aggregate))
    results.extend(_health_gates_for(aggregate))
    results.extend(gates.process_lifecycle_gates(aggregate.lifecycle_readings))
    results.extend(gates.resource_trend_gates(aggregate.trend_readings))
    # Guarded, because headroom_gates([]) returns ONE row with no subject at
    # all, and calling it twice on a window that measured neither produced two
    # identical unidentified rows per step. That is the subject collision this
    # file has already fixed twice: once by putting the source in the subject,
    # once by adding the step. A criterion nothing measured is reported ONCE per
    # run instead, by unmeasured_resource_gates below.
    if aggregate.rtp_port_readings:
        results.extend(gates.headroom_gates(aggregate.rtp_port_readings))
    if aggregate.allowance_readings:
        results.extend(gates.network_allowance_gates(aggregate.allowance_readings))
    results.extend(_bandwidth_gates_for(aggregate, network_baselines or {}))
    return results


#: How much of a declared baseline may be used before headroom is breached.
#: The contracted criterion is 20% remaining, and the headroom gate expresses
#: that as a floor on what is LEFT, so nothing here restates 0.80.
def _bandwidth_gates_for(
    aggregate: TestAggregate,
    baselines: dict[str, dict[str, float]],
) -> list[GateResult]:
    """Peak throughput against the instance type's PUBLISHED baseline.

    The only way the contracted "20% remaining" is obtainable for network. There
    is no measurable link capacity: the ENA driver reports no speed, so
    node_network_speed_bytes yields nothing for the real interface.

    So the denominator is DECLARED, and a node nobody declared one for is
    UNKNOWN rather than measured against an inferred figure. The instance type
    is never read from a string to look a number up: that would be a guess
    wearing a measurement's clothes.

    Inbound and outbound are separate rows because the hypervisor maintains
    separate credit buckets, and a node can saturate one direction while the
    other idles.
    """
    if not aggregate.bandwidth_peaks:
        return []
    readings: list[gates.HeadroomReading] = []
    for (node, direction), peak_bps in sorted(aggregate.bandwidth_peaks.items()):
        resource = (
            gates.HEADROOM_NETWORK_IN
            if direction == "in"
            else gates.HEADROOM_NETWORK_OUT
        )
        declared = baselines.get(node) or {}
        baseline_bps = declared.get(f"{direction}_bps")
        if not baseline_bps:
            readings.append(
                gates.HeadroomReading(
                    node=node,
                    resource=resource,
                    used=None,
                    limit=None,
                    unmeasured_reason=(
                        f"{node} carried throughput but no "
                        f"{direction}bound baseline was declared for it, and "
                        "there is nothing to measure link capacity against. The "
                        "instance type is deliberately not read to infer one"
                    ),
                )
            )
            continue
        readings.append(
            gates.HeadroomReading(
                node=node,
                resource=resource,
                used=peak_bps,
                limit=baseline_bps,
            )
        )
    return gates.headroom_gates(readings)


#: The most a resource may end at, as a MULTIPLE of its idle baseline.
#:
#: An absolute ratio ceiling, not a percentage band, because that is what
#: return_to_baseline_gates compares against: it computes post/baseline and
#: fails above this. Reading it as "within 10%" and passing 0.10 means "must
#: fall to a tenth of baseline", which fails a node that ended exactly where it
#: started. That mistake was made here and it produced twenty-one FAIL rows on
#: a real run, including 4 sockets against a baseline of 4.
#:
#: 1.10 is OURS, not contracted. The criterion says "close to baseline" and
#: never quantifies it, and return_to_baseline_gates refuses to default a
#: tolerance precisely so a number nobody agreed to cannot look like one that
#: was agreed. This is the caller stating it out loud.
BASELINE_TOLERANCE = 1.10


def _run_baseline_gates(
    aggregates: dict[str, TestAggregate | None],
) -> list[GateResult]:
    """Did the fleet give its resources back after the CALLS ended?

    Once per run, and this is not tidying: per test it compared each step
    against the step before it, so "baseline" for step four was step three under
    load. The criterion is about idle before the run against settled after it.

    So the BEFORE side is taken from the earliest test's aggregate and the AFTER
    side from the latest, matched per node and metric. Anything with only one
    side stays UNKNOWN, which is the honest reading: nobody established what
    baseline was, so nothing can be shown to have returned to it.
    """
    ordered = [a for a in aggregates.values() if a is not None]
    if not ordered:
        return []
    ordered.sort(key=lambda a: a.window.start_ms)
    first, last = ordered[0], ordered[-1]
    befores = {(c.node, c.metric): c for c in first.baseline_comparisons}
    afters = {(c.node, c.metric): c for c in last.baseline_comparisons}
    keys = sorted(set(befores) | set(afters))
    if not keys:
        return []
    merged = []
    for key in keys:
        before, after = befores.get(key), afters.get(key)
        node, metric = key
        merged.append(
            gates.BaselineComparison(
                node=node,
                metric=metric,
                baseline=before.baseline if before else None,
                post_settle=after.post_settle if after else None,
                baseline_at_ms=before.baseline_at_ms if before else None,
                post_settle_at_ms=after.post_settle_at_ms if after else None,
                unmeasured_reason=(
                    (before.unmeasured_reason if before else None)
                    or (after.unmeasured_reason if after else None)
                ),
            )
        )
    return list(
        gates.return_to_baseline_gates(merged, tolerance=BASELINE_TOLERANCE)
    )


def _health_gates_for(aggregate: TestAggregate) -> list[GateResult]:
    """Sustained Redis and health-endpoint failures, per node per source.

    ONLY what was actually sampled. The "nothing was configured" case is a fact
    about the RUN, not about each step, and lives in
    :func:`unconfigured_dependency_gates` so it is stated once. Emitting it here
    put fourteen identical rows in a seven-step report, which is the same defect
    that rtp_ports and network already had fixed.
    """
    return list(gates.sustained_health_gates(aggregate.health_readings))


def unconfigured_dependency_gates(seen: set[str]) -> list[GateResult]:
    """One row per dependency criterion nobody wired, once per run.

    ``seen`` holds BARE dependency constants (:data:`gates.HEALTH_SUBJECT_REDIS`
    and :data:`gates.HEALTH_SUBJECT_ENDPOINT`) as they appear on a
    ``HealthSeriesReading``, NOT rendered gate subjects like
    ``sip-1/redis-exporter/redis``. Passing the latter silently matches nothing
    and every dependency reads as unconfigured even when it was measured.

    NOT CONFIGURED IS NOT A PASS, and it is not silence either. The criterion
    was agreed, so a report that omits it reads as one nobody asked about, and a
    run that never looked at Redis must never be indistinguishable from one that
    checked and found it healthy.

    The reason names BOTH ways out, because an operator reading UNKNOWN needs to
    know which they are looking at: wire the source, or declare it absent with a
    written waiver a reviewer can read. A single-node deployment genuinely has
    no Redis, and saying so once in config is a true statement; silence is a
    vacuous one.
    """
    results: list[GateResult] = []
    for subject, what, wire in (
        (
            gates.HEALTH_SUBJECT_REDIS,
            "no scrape target uses the redis-exporter source",
            "add a redis-exporter scrape target",
        ),
        (
            gates.HEALTH_SUBJECT_ENDPOINT,
            "no scrape target declares a health endpoint",
            "attach a health endpoint to a target with the |health= suffix",
        ),
    ):
        if subject in seen:
            continue
        results.append(
            gates.sustained_health_gate(
                gates.HealthSeriesReading(
                    node=gates.FLEET_SUBJECT,
                    subject=subject,
                    unmeasured_reason=(
                        f"{what}, so this criterion was not evaluated. It is "
                        "NOT passing: nothing was asked and nothing answered. "
                        "There are exactly two ways out and silence is neither: "
                        f"WIRE it ({wire}), or DECLARE it absent by waiving this "
                        "gate in writing with a reason. A waiver is recorded and "
                        "a reviewer reads it; an unevaluated criterion just "
                        "looks like one nobody agreed to."
                    ),
                )
            )
        )
    return results


def _headroom_gates_for(aggregate: TestAggregate) -> list[GateResult]:
    """Headroom for every node in the window, measured where possible.

    File descriptors are the one resource with a scraped pair. RTP ports and
    network have no series at all, and
    :func:`gates.unscraped_headroom_readings` is what puts them in the report as
    UNKNOWN so their absence is legible rather than invisible.
    """

    readings: list[gates.HeadroomReading] = []
    # Real file-descriptor readings, measured during correlation. A node whose
    # window carried no usable pair arrives here already unmeasured WITH its
    # reason, which the gate turns into UNKNOWN rather than a pass.
    #
    # Authority first, and for a DEPENDENCY exporter it is unconditional. A
    # number from redis-exporter's own process is not weak evidence about the
    # fleet, it is evidence about a monitoring sidecar, and grading it would put
    # a sidecar's descriptor headroom in a client report as if it answered the
    # criterion. "Never discard a measurement" protects readings that are about
    # the subject; it does not promote one that never was.
    #
    # A source merely UNKNOWN to the classifier keeps the old rule: a real
    # reading is graded whatever took it, because there the absence of an answer
    # is ignorance rather than a category error.
    readings.extend(
        r
        for r in aggregate.fd_readings
        if _speaks_for_process_fds(r.source)
        or (r.used is not None and reports_process_metrics(r.source) is None)
    )
    measured = {(r.node, r.source) for r in aggregate.fd_readings}
    for reading in aggregate.cpu_readings:
        if not _speaks_for_process_fds(reading.source):
            continue
        # A node the aggregate judged for CPU but carries no FD reading for
        # would otherwise lose the file-descriptor gate while keeping the other
        # two, which reads as a resource nobody had to satisfy rather than one
        # nobody measured.
        if (reading.node, reading.source) not in measured:
            readings.append(
                _fd_reading(reading.node, reading.source, reason=_NOT_ON_AGGREGATE)
            )
        # RTP ports and network are NOT emitted here. Nothing can measure
        # either on any node, so a per-node row is the same non-fact repeated
        # once per test per source. They are reported once, as scope exclusions,
        # by excluded_headroom_resources.
    if not readings:
        # Nothing was scraped at all. Every resource is still reported, so an
        # unscraped run reads as three UNKNOWN gates rather than as a run where
        # file descriptors quietly went unjudged while the other two did not.
        readings = [_fd_reading(gates.FLEET_SUBJECT, reason=_NO_SAMPLES)]
    return gates.headroom_gates(readings)


def judge_run(
    tests: list[dict[str, Any]],
    *,
    aggregates: dict[str, TestAggregate | None] | None = None,
    network_baselines: dict[str, dict[str, float]] | None = None,
) -> list[GateResult]:
    """Every gate for every test in a run, flattened in test order.

    ``aggregates`` maps a test name to what was correlated to its window. A test
    missing from the mapping is judged as having no window, which is the honest
    reading: nothing correlated to it.
    """
    aggregates = aggregates or {}
    out: list[GateResult] = []
    for test in tests:
        name = str(test.get("name") or "test")
        # Stamped HERE and not inside judge_test, because the step is run-level
        # identity: judge_test grades one step and has no idea whether it is one
        # of seven. Identity only, so nothing about the verdict moves.
        out.extend(
            replace(result, step=name)
            for result in judge_test(
                test,
                aggregate=aggregates.get(name),
                network_baselines=network_baselines,
            )
        )
    # ONCE PER RUN, not once per node per test. Nothing measures these on any
    # node, so eighteen identical rows on a three-step ramp said the same two
    # things eighteen times, above the table the client contracted for.
    #
    # Still GATES rather than a note, because a gate is the only thing a written
    # waiver can attach to. The checklist requires a threshold nobody funded to
    # be recorded as waived, with a reason and by whom, and an exclusion with no
    # gate has nothing to sign. They stay UNKNOWN until somebody does that, so
    # the verdict does not improve on its own either.
    out.extend(unmeasurable_headroom_gates())
    # ONCE PER RUN, for the same reason. A dependency nobody wired is a fact
    # about the deployment, not about step four of seven.
    out.extend(
        unconfigured_dependency_gates(
            {
                r.subject
                for agg in aggregates.values()
                if agg is not None
                for r in agg.health_readings
            }
        )
    )
    # Same rule for the three lifecycle criteria: a contracted line that
    # produced no row anywhere in the run is reported once, as unmeasured. This
    # is the defect these gates exist to remove, and it would be quietly
    # recreated by a run whose samples carried none of the needed columns.
    out.extend(_run_baseline_gates(aggregates))
    out.extend(unevaluated_criteria_gates({g.gate for g in out}))
    out.extend(unmeasured_resource_gates(out))
    return out


def unmeasured_resource_gates(emitted: list[GateResult]) -> list[GateResult]:
    """One fleet row per newly measurable criterion that measured nothing.

    These resources stopped being scope exclusions the moment the columns were
    wired, which is correct: they ARE measurable now. But a run whose scrape
    carried none of the new series would then have emitted no row for them at
    all, and a contracted criterion that produces no row reads as one nobody
    agreed to. That is the defect these gates exist to remove, recreated one
    level down.

    Once per RUN, not per step, and keyed on the SUBJECT rather than the gate
    id: resource_headroom is shared with file descriptors, which always emits,
    so a gate-id check would never fire.
    """
    seen = {(g.subject or "").rsplit("/", 1)[-1] for g in emitted}
    results: list[GateResult] = []
    missing = [
        resource
        for resource in (
            gates.HEADROOM_RTP_PORTS,
            gates.HEADROOM_NETWORK_IN,
            gates.HEADROOM_NETWORK_OUT,
        )
        if resource not in seen
    ]
    if missing:
        results.extend(
            gates.headroom_gates(
                [
                    gates.HeadroomReading(
                        node=gates.FLEET_SUBJECT,
                        resource=resource,
                        used=None,
                        limit=None,
                        # No "not measured" prefix here: headroom_gate already
                        # opens with "was not measured:", and carrying it twice
                        # printed the phrase back to back in a client report.
                        unmeasured_reason=(
                            "nothing in this run's scrape carried the series it "
                            "needs. The criterion is measurable, so this is a "
                            "gap in what was collected rather than a limit of "
                            "the system"
                        ),
                    )
                    for resource in missing
                ]
            )
        )
    if not any(g.gate == gates.NETWORK_ALLOWANCE_GATE for g in emitted):
        results.append(
            gates.network_allowance_gate(
                gates.AllowanceReading(node=gates.FLEET_SUBJECT)
            )
        )
    return results


def unevaluated_criteria_gates(seen: set[str]) -> list[GateResult]:
    """One fleet-level row per contracted criterion that produced nothing.

    ``seen`` is the set of gate IDS already emitted for the run. A criterion
    that produced even one row somewhere is not reported here: the rows it did
    produce are the answer, including any UNKNOWN ones.

    Once per RUN, not once per step, for the reason the dependency rows are:
    "nothing carried the columns for this" is a fact about the deployment.
    """
    results: list[GateResult] = []
    if gates.PROCESS_LIFECYCLE_GATE not in seen:
        results.append(
            gates.process_lifecycle_gate(
                gates.LifecycleReading(node=gates.FLEET_SUBJECT)
            )
        )
    if gates.RESOURCE_TREND_GATE not in seen:
        results.append(
            gates.resource_trend_gate(
                gates.TrendReading(
                    node=gates.FLEET_SUBJECT, metric="stale_resources"
                )
            )
        )
    if gates.RETURN_TO_BASELINE_GATE not in seen:
        results.extend(
            gates.return_to_baseline_gates(
                [
                    gates.BaselineComparison(
                        node=gates.FLEET_SUBJECT,
                        metric=metric,
                        baseline=None,
                        post_settle=None,
                        unmeasured_reason=(
                            "not measured: no sample outside the test window "
                            "carried this resource, so no baseline was "
                            "established and nothing shows it came back"
                        ),
                    )
                    for metric in BASELINE_METRICS
                ],
                tolerance=BASELINE_TOLERANCE,
            )
        )
    return results


def unmeasurable_headroom_gates() -> list[GateResult]:
    """One fleet-level gate per headroom resource nothing can measure.

    Fleet-level because the fact is fleet-level: no exporter anywhere publishes
    what these need, so naming a node would imply the answer could differ by
    node. The subject is stable so a waiver can name it.
    """
    excluded = excluded_headroom_resources()
    if not excluded:
        return []
    # A SHORT reason on the row. The full statement lives once, in the scope
    # section above the table, and repeating the paragraph in every gate detail
    # printed it twice on one page and ran two sentences together mid-cell.
    # The row says what it is and where the detail is.
    return gates.headroom_gates(
        [
            gates.HeadroomReading(
                node=gates.FLEET_SUBJECT,
                resource=resource,
                used=None,
                limit=None,
                unmeasured_reason=(
                    "nothing in the scrape set publishes what it needs, so it is "
                    'outside scope for this report (see "Not in scope")'
                ),
            )
            for resource in sorted(excluded)
        ]
    )


def verdict_for(results: list[GateResult]) -> str:
    """The run-level verdict. Read from the gates, never recomputed."""
    return gates.verdict(results)


__all__ = [
    "excluded_headroom_resources",
    "judge_run",
    "judge_test",
    "unmeasurable_headroom_gates",
    "verdict_for",
]
