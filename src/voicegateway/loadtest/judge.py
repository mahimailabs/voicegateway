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
    FD_LIMIT_COLUMN,
    FD_USED_COLUMN,
    TestAggregate,
)
from voicegateway.middleware.node_samples_worker_middleware import (
    any_source_publishes,
    reports_host_metrics,
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
    gates.HEADROOM_RTP_PORTS: ("sockstat_udp_inuse", "rtp_port_range_size"),
    gates.HEADROOM_NETWORK: (
        "network_throughput_bytes",
        "network_link_capacity_bytes",
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
    excluded: dict[str, str] = {}
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
        )
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
        return results

    # Filtered by what the SOURCE can publish, not by what this run produced.
    # Per-node reporting is untouched: at 500 concurrent across six SIP nodes,
    # knowing WHICH node breached is the point, so nothing here collapses rows.
    results.extend(_graded(aggregate.cpu_readings, gates.node_cpu_gates))
    results.extend(_graded(aggregate.memory_readings, gates.node_memory_gates))
    results.extend(_headroom_gates_for(aggregate))
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
    readings.extend(aggregate.fd_readings)
    measured = {(r.node, r.source) for r in aggregate.fd_readings}
    for reading in aggregate.cpu_readings:
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
            for result in judge_test(test, aggregate=aggregates.get(name))
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
    return out


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
