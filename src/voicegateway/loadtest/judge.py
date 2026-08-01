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
from voicegateway.loadtest.aggregation import TestAggregate

# The FD pair is the one headroom resource anything scrapes. RTP ports and
# network are emitted as unmeasured by unscraped_headroom_readings, so they
# appear in every report as UNKNOWN instead of vanishing.
_FD_USED = "filefd_allocated"
_FD_LIMIT = "filefd_maximum"


def _fd_reading(node: str, source: str | None = None) -> gates.HeadroomReading:
    """File descriptors as an unmeasured reading.

    The pair IS scraped into node_samples, but it is not threaded onto the
    per-test aggregate yet, so it is reported as unmeasured rather than guessed
    at. Emitted on every path so an unscraped run shows three UNKNOWN headroom
    gates rather than two, with file descriptors quietly unjudged.
    """
    return gates.HeadroomReading(
        node=node,
        resource="file_descriptors",
        used=None,
        limit=None,
        source=source,
        unmeasured_reason=(
            f"{_FD_USED}/{_FD_LIMIT} are scraped but are not carried on the "
            "per-test aggregate yet"
        ),
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
    for reading in aggregate.cpu_readings:
        # The FD pair is not carried on TestAggregate, so it is reported as
        # unmeasured here rather than guessed at. Wiring it needs the gauge
        # pair threaded through aggregation, which is a measurement change and
        # does not belong in a module that only judges.
        readings.append(_fd_reading(reading.node, reading.source))
        readings.extend(
            gates.unscraped_headroom_readings(reading.node, source=reading.source)
        )
    if not readings:
        # Nothing was scraped at all. Every resource is still reported, so an
        # unscraped run reads as three UNKNOWN gates rather than as a run where
        # file descriptors quietly went unjudged while the other two did not.
        readings = [_fd_reading("fleet"), *gates.unscraped_headroom_readings("fleet")]
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
