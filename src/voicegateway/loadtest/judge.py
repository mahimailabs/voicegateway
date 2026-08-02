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

from typing import Any

from voicegateway.livekit_diag import gates
from voicegateway.livekit_diag.gates import GateResult
from voicegateway.loadtest.aggregation import (
    FD_LIMIT_COLUMN,
    FD_USED_COLUMN,
    TestAggregate,
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
        results.extend(
            gates.headroom_gates(
                [
                    _fd_reading(target),
                    *gates.unscraped_headroom_readings(target),
                ]
            )
        )
        return results

    results.extend(gates.node_cpu_gates(aggregate.cpu_readings))
    results.extend(gates.node_memory_gates(aggregate.memory_readings))
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
        readings.extend(
            gates.unscraped_headroom_readings(reading.node, source=reading.source)
        )
    if not readings:
        # Nothing was scraped at all. Every resource is still reported, so an
        # unscraped run reads as three UNKNOWN gates rather than as a run where
        # file descriptors quietly went unjudged while the other two did not.
        readings = [
            _fd_reading("fleet", reason=_NO_SAMPLES),
            *gates.unscraped_headroom_readings("fleet"),
        ]
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
        out.extend(judge_test(test, aggregate=aggregates.get(name)))
    return out


def verdict_for(results: list[GateResult]) -> str:
    """The run-level verdict. Read from the gates, never recomputed."""
    return gates.verdict(results)


__all__ = ["judge_run", "judge_test", "verdict_for"]
