"""A gate is emitted where the measurement was possible, and nowhere else.

A real run produced 48 gate rows, 33 of them UNKNOWN, above the three-row table
the client contracted for. Fifteen of those 33 were category errors rather than
disclosure: node CPU and node memory graded UNKNOWN for livekit-sip and
livekit-server, which publish neither and never will, and file descriptors
graded UNKNOWN for node-exporter, which publishes no process FDs. Nobody
attempted those measurements. Grading them says a measurement failed.

**The distinction this file exists to hold.** Two things look identical from the
row in front of you, and only one may be suppressed:

* livekit-sip does not export node CPU. There is no run in which it would.
  Verified against a live one: zero node-wide series.
* node-exporter exports node CPU and produced nothing this window. A scraper
  that died mid-run is exactly what UNKNOWN exists to surface.

**Only node-wide CPU and memory are suppressed, and the narrowness is the
lesson.** A first attempt keyed the decision off the SERIES map, on the theory
that a source not wired for a column cannot produce it. That is false, and a
pre-existing test caught it: node_exporter publishes process_open_fds for its
own process (a live one reads 9), and the map omits it deliberately because
node_exporter's own file handles say nothing about livekit-sip's headroom. The
map encodes which subject each source is the AUTHORITY for, not what it is
capable of, and reading it as capability suppressed a file-descriptor gate for a
metric the source could have produced.
"""

from __future__ import annotations

import pytest

from voicegateway.livekit_diag import gates
from voicegateway.loadtest import judge

# Aliased: pytest tries to collect any module-level name starting with
# "Test" as a test class, and warns when it has a constructor.
from voicegateway.loadtest.aggregation import TestAggregate as _Aggregate
from voicegateway.middleware.node_samples_worker_middleware import (
    SERIES,
    reports_host_metrics,
)
from voicegateway.repository.node_correlation_repository import window_of

HEALTHY = {"name": "ramp-20", "attempted_calls": 100, "succeeded_calls": 100}
WINDOW = window_of(1_785_661_201_000, 1_785_661_260_000)


def _cpu(node: str, source: str | None, utilisation: float | None):
    return gates.NodeUtilisationReading(
        node=node,
        source=source,
        utilisation=utilisation,
        samples=12 if utilisation is not None else 0,
        unmeasured_reason=None if utilisation is not None else "nothing in window",
    )


def _fd(node: str, source: str | None, used: float | None):
    return gates.HeadroomReading(
        node=node,
        source=source,
        resource=gates.HEADROOM_FILE_DESCRIPTORS,
        used=used,
        limit=524287.0 if used is not None else None,
        unmeasured_reason=None if used is not None else "nothing in window",
    )


def _aggregate(*, cpu=(), memory=(), fds=()) -> _Aggregate:
    return _Aggregate(
        window=WINDOW,
        peak_cpu_utilisation=None,
        peak_memory_utilisation=None,
        node_samples_in_window=len(cpu),
        cpu_readings=list(cpu),
        memory_readings=list(memory),
        fd_readings=list(fds),
    )


def _gates_named(results, name: str):
    return [r for r in results if r.gate == name]


# --------------------------------------------------------------------------
# The premise, checked against the map rather than assumed
# --------------------------------------------------------------------------


def test_the_sources_really_do_differ_in_what_they_publish() -> None:
    """Guards every test below. If this changes, the suppression must too.

    Both answers were checked against live exporters, not against the map:
    node_exporter publishes node_cpu_seconds_total, livekit-sip publishes no
    node-wide series at all.
    """
    assert reports_host_metrics("node-exporter") is True
    assert reports_host_metrics("livekit-sip") is False
    assert reports_host_metrics("livekit-server") is False


# --------------------------------------------------------------------------
# Structurally impossible: no gate
# --------------------------------------------------------------------------


def test_a_livekit_sip_target_produces_no_cpu_gate_at_all() -> None:
    """Required by the node. It publishes no node CPU series, ever."""
    results = judge.judge_test(
        HEALTHY, aggregate=_aggregate(cpu=[_cpu("sip-1", "livekit-sip", None)])
    )
    assert _gates_named(results, "node_cpu") == []


def test_a_livekit_sip_target_produces_no_memory_gate_either() -> None:
    results = judge.judge_test(
        HEALTHY, aggregate=_aggregate(memory=[_cpu("sip-1", "livekit-sip", None)])
    )
    assert _gates_named(results, "node_memory") == []


def test_node_exporter_keeps_its_file_descriptor_gate() -> None:
    """The correction. It CAN publish process FDs, so an absent one is UNKNOWN.

    A first version suppressed this, reasoning from the SERIES map not wiring
    the column for node-exporter. But a live node_exporter reports
    process_open_fds 9: the map omits it because those handles belong to the
    exporter rather than to any service under test, which is a relevance
    decision and not an inability. Suppressing on it drops a gate for a metric
    the source could have produced.
    """
    results = judge.judge_test(
        HEALTHY,
        aggregate=_aggregate(
            cpu=[_cpu("sfu-1", "node-exporter", 0.5)],
            fds=[_fd("sfu-1", "node-exporter", None)],
        ),
    )
    [fd_gate] = [
        r
        for r in _gates_named(results, "resource_headroom")
        if r.subject and r.subject.endswith("/file_descriptors")
    ]
    assert fd_gate.status == gates.UNKNOWN


# --------------------------------------------------------------------------
# Possible and absent: STILL UNKNOWN. The trap.
# --------------------------------------------------------------------------


def test_a_node_exporter_absent_from_the_window_still_produces_unknown() -> None:
    """Required by the node, and the reason this cannot read the null.

    node-exporter publishes CPU. A window with nothing in it means the scrape
    failed, which is the single most important thing a gate can tell an
    operator, and it must survive a change whose entire purpose is removing
    rows.
    """
    results = judge.judge_test(
        HEALTHY, aggregate=_aggregate(cpu=[_cpu("sfu-1", "node-exporter", None)])
    )
    [gate] = _gates_named(results, "node_cpu")
    assert gate.status == gates.UNKNOWN
    assert gate.subject == "sfu-1/node-exporter"


def test_a_livekit_sip_absent_from_the_window_still_loses_its_fd_gate_never() -> None:
    """livekit-sip DOES publish process FDs, so a missing reading is UNKNOWN."""
    results = judge.judge_test(
        HEALTHY,
        aggregate=_aggregate(
            cpu=[_cpu("sip-1", "livekit-sip", None)],
            fds=[_fd("sip-1", "livekit-sip", None)],
        ),
    )
    [fd_gate] = [
        r
        for r in _gates_named(results, "resource_headroom")
        if r.subject and r.subject.endswith("/file_descriptors")
    ]
    assert fd_gate.status == gates.UNKNOWN


def test_suppression_does_not_read_the_value() -> None:
    """Stated directly: same null, two sources, two different answers.

    If the decision ever moves to the row in front of it, these two cases become
    indistinguishable and the bug returns wearing a passing test.
    """
    capable = judge.judge_test(
        HEALTHY, aggregate=_aggregate(cpu=[_cpu("sfu-1", "node-exporter", None)])
    )
    incapable = judge.judge_test(
        HEALTHY, aggregate=_aggregate(cpu=[_cpu("sip-1", "livekit-sip", None)])
    )
    assert len(_gates_named(capable, "node_cpu")) == 1
    assert len(_gates_named(incapable, "node_cpu")) == 0


# --------------------------------------------------------------------------
# Unknown sources keep their gates
# --------------------------------------------------------------------------


@pytest.mark.parametrize("source", [None, "some-exporter-added-later"])
def test_an_undeclared_source_is_not_suppressed(source) -> None:
    """Silence about a source nobody declared is not evidence it measures nothing.

    Treating unknown as incapable would silently drop gates for every exporter
    added after this: the same mistake with a longer fuse.
    """
    assert source not in SERIES
    results = judge.judge_test(
        HEALTHY, aggregate=_aggregate(cpu=[_cpu("new-1", source, None)])
    )
    assert len(_gates_named(results, "node_cpu")) == 1


# --------------------------------------------------------------------------
# Per-node reporting is untouched
# --------------------------------------------------------------------------


def test_every_node_still_gets_its_own_row() -> None:
    """At 500 concurrent across six nodes, WHICH node breached is the point.

    Suppression is per source, never per metric, so a fleet does not collapse
    into one summary row.
    """
    fleet = [_cpu(f"sfu-{i}", "node-exporter", 0.4 + i * 0.1) for i in range(6)]
    results = judge.judge_test(HEALTHY, aggregate=_aggregate(cpu=fleet))
    cpu_gates = _gates_named(results, "node_cpu")
    assert len(cpu_gates) == 6
    assert {g.subject for g in cpu_gates} == {
        f"sfu-{i}/node-exporter" for i in range(6)
    }


def test_one_breaching_node_is_visible_among_healthy_ones() -> None:
    """The reason per-node rows exist at all."""
    fleet = [
        _cpu("sfu-1", "node-exporter", 0.40),
        _cpu("sfu-2", "node-exporter", 0.95),
        _cpu("sfu-3", "node-exporter", 0.45),
    ]
    results = judge.judge_test(HEALTHY, aggregate=_aggregate(cpu=fleet))
    failed = [g for g in _gates_named(results, "node_cpu") if g.status == gates.FAIL]
    assert [g.subject for g in failed] == ["sfu-2/node-exporter"]


# --------------------------------------------------------------------------
# The shape of a real run
# --------------------------------------------------------------------------


def test_the_category_error_rows_are_gone_from_a_realistic_run() -> None:
    """Three sources on one node, as the live deployment reported.

    Before: node CPU and memory graded for all three sources, and file
    descriptors graded for all three. After: each metric appears only where an
    exporter publishes it.
    """
    sources = ("livekit-server", "livekit-sip", "node-exporter")
    aggregate = _aggregate(
        cpu=[_cpu("box-1", s, 0.66 if s == "node-exporter" else None) for s in sources],
        memory=[
            _cpu("box-1", s, 0.61 if s == "node-exporter" else None) for s in sources
        ],
        fds=[_fd("box-1", s, None if s == "node-exporter" else 11.0) for s in sources],
    )
    results = judge.judge_test(HEALTHY, aggregate=aggregate)

    assert [g.subject for g in _gates_named(results, "node_cpu")] == [
        "box-1/node-exporter"
    ]
    assert [g.subject for g in _gates_named(results, "node_memory")] == [
        "box-1/node-exporter"
    ]
    # File descriptors survive for ALL THREE. Every source is a process with
    # file handles, so an absent reading is a gap rather than a category error.
    fd_subjects = {
        g.subject
        for g in _gates_named(results, "resource_headroom")
        if g.subject and g.subject.endswith("/file_descriptors")
    }
    assert fd_subjects == {f"box-1/{source}/file_descriptors" for source in sources}
    # And nothing that WAS measured lost its grade.
    assert {g.status for g in _gates_named(results, "node_cpu")} == {gates.PASS}


def test_an_entirely_unscraped_window_still_reports_unknown() -> None:
    """The other empty case, and it must NOT be quietly removed.

    Nothing scraped at all is a real finding: the window produced no samples and
    the run demonstrated no ceiling. Only "something was scraped, none of it
    from a source that publishes this" yields no gate.
    """
    results = judge.judge_test(HEALTHY, aggregate=_aggregate())
    [cpu_gate] = _gates_named(results, "node_cpu")
    assert cpu_gate.status == gates.UNKNOWN
    assert "no node was sampled" in cpu_gate.detail


def test_the_two_empty_cases_differ() -> None:
    """Said once, directly, because collapsing them is the easy mistake."""
    nothing_scraped = judge.judge_test(HEALTHY, aggregate=_aggregate())
    scraped_but_incapable = judge.judge_test(
        HEALTHY, aggregate=_aggregate(cpu=[_cpu("sip-1", "livekit-sip", None)])
    )
    assert len(_gates_named(nothing_scraped, "node_cpu")) == 1
    assert len(_gates_named(scraped_but_incapable, "node_cpu")) == 0
