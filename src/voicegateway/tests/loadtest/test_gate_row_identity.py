"""No two gate rows in a report are indistinguishable.

A real seven-step run rendered seven rows reading::

    UNKNOWN | resource_headroom  sura-sip-01/node-exporter/file_descriptors | ...

identically, with nothing saying which step each belonged to. A reader cannot
tell whether that is one problem or seven, and the next run is a fleet: more
steps and four nodes instead of one, so every per-step per-node resource gate
multiplies the same way.

This is the same collision class already fixed once for SOURCES. Three exporters
on one node produced three rows labelled identically reading PASS, UNKNOWN and
UNKNOWN, and the fix was to put the source into the subject. The step is the
other half of that identity and was still missing.

**Identity only.** Nothing here changes which gates are emitted or how any of
them grades, and the tests below pin that explicitly: the statuses, the verdict
and the row count are asserted to be exactly what they were before the step
existed. If making rows unique had required suppressing a row or moving a
status, that would have been a different change needing a different decision.
"""

from __future__ import annotations

from voicegateway.livekit_diag import gates, run_report
from voicegateway.loadtest import judge

# Aliased: pytest collects any module-level name starting with "Test".
from voicegateway.loadtest.aggregation import TestAggregate as _Aggregate
from voicegateway.repository.node_correlation_repository import window_of

WINDOW = window_of(1_785_661_201_000, 1_785_661_260_000)

# A seven-step ramp, the shape that produced the collision.
STEPS = [
    {
        "name": f"ramp-{n}",
        "attempted_calls": 100,
        "succeeded_calls": 100,
        "sequence": i,
    }
    for i, n in enumerate((5, 10, 15, 20, 25, 30, 35))
]

# The collision only exists once nodes were actually correlated. With no
# aggregate the judge falls back to using the step name as the subject, so the
# rows are already unique and a fixture without one proves nothing. The real run
# scraped ONE node under three sources on every step, which is what makes the
# per-node subjects identical across steps.
NODE = "sura-sip-01"
SOURCES = ("node-exporter", "livekit-sip", "livekit-server")


def _aggregate() -> _Aggregate:
    cpu = [
        gates.NodeUtilisationReading(
            node=NODE,
            source=source,
            utilisation=0.42 if source == "node-exporter" else None,
            samples=12 if source == "node-exporter" else 0,
            unmeasured_reason=None
            if source == "node-exporter"
            else "publishes no node-wide series",
        )
        for source in SOURCES
    ]
    fds = [
        gates.HeadroomReading(
            node=NODE,
            source=source,
            resource=gates.HEADROOM_FILE_DESCRIPTORS,
            used=120.0 if source != "node-exporter" else None,
            limit=524287.0 if source != "node-exporter" else None,
            unmeasured_reason=None
            if source != "node-exporter"
            else "no row in the window carried both",
        )
        for source in SOURCES
    ]
    return _Aggregate(
        window=WINDOW,
        peak_cpu_utilisation=0.42,
        peak_memory_utilisation=0.5,
        node_samples_in_window=len(cpu),
        cpu_readings=cpu,
        memory_readings=list(cpu),
        fd_readings=fds,
    )


AGGREGATES = {step["name"]: _aggregate() for step in STEPS}


def _run():
    return judge.judge_run(STEPS, aggregates=AGGREGATES)


def _identity(gate) -> tuple:
    """What a reader can use to tell one row from another."""
    return (gate.gate, gate.step, gate.subject)


# --------------------------------------------------------------------------
# The property
# --------------------------------------------------------------------------


def test_every_row_in_a_multi_step_run_is_distinguishable() -> None:
    """The whole node, stated once."""
    results = _run()
    identities = [_identity(g) for g in results]
    duplicates = {i for i in identities if identities.count(i) > 1}
    assert not duplicates, duplicates


def test_the_step_is_what_makes_them_unique() -> None:
    """Non-vacuous. Without the step these rows really do collide.

    Guards against the test passing for some unrelated reason, such as the
    subjects happening to differ already.
    """
    results = _run()
    per_step = [g for g in results if g.step is not None]
    without_step = [(g.gate, g.subject) for g in per_step]
    assert len(set(without_step)) < len(without_step)
    assert len({_identity(g) for g in per_step}) == len(per_step)


def test_every_per_step_row_names_its_step() -> None:
    names = {s["name"] for s in STEPS}
    for gate in _run():
        if gate.step is not None:
            assert gate.step in names, gate


def test_run_level_rows_carry_no_step() -> None:
    """The fleet exclusions are evaluated once per RUN, not once per step.

    Stamping a step on them would claim seven evaluations where there was one,
    which is the same lie in the opposite direction.
    """
    run_level = [g for g in _run() if g.step is None]
    assert {g.subject for g in run_level} == {
        "fleet/network",
        "fleet/rtp_ports",
        # Same shape, added with the dependency criteria: a dependency nobody
        # wired is a fact about the deployment, not about step four of seven.
        "fleet/redis",
        "fleet/health_endpoint",
        # The three lifecycle criteria, reported once per run for the same
        # reason: "nothing carried the columns for this" is a fact about the
        # deployment, not about step four of seven.
        "fleet",
        "fleet/stale_resources",
        "fleet/memory_used_bytes",
        "fleet/filefd_allocated",
        "fleet/sockstat_udp_inuse",
    }


# --------------------------------------------------------------------------
# Nothing about the judgement moved
# --------------------------------------------------------------------------


def test_the_row_count_is_unchanged() -> None:
    """Identity, not emission. Seven steps still produce what they produced."""
    results = _run()
    per_step = [g for g in results if g.step is not None]
    assert len(per_step) == len(
        judge.judge_test(STEPS[0], aggregate=AGGREGATES[STEPS[0]["name"]])
    ) * len(STEPS)


def test_no_status_moved() -> None:
    """Each step grades exactly as it did when judged on its own."""
    stamped = [g for g in _run() if g.step == "ramp-25"]
    alone = judge.judge_test(
        next(s for s in STEPS if s["name"] == "ramp-25"),
        aggregate=AGGREGATES["ramp-25"],
    )
    assert [g.status for g in stamped] == [g.status for g in alone]
    assert [g.detail for g in stamped] == [g.detail for g in alone]


def test_the_verdict_is_unchanged() -> None:
    results = _run()
    assert judge.verdict_for(results) == gates.UNKNOWN


# --------------------------------------------------------------------------
# The payload shape contract
# --------------------------------------------------------------------------


def test_a_gate_with_no_step_keeps_its_exact_key_set() -> None:
    """A diagnostics gate belongs to no step and must not grow a key.

    The payload shape is persisted with each run and served to the dashboard, so
    only a row that really is one of several per run may grow.
    """
    checks = {"agents": {"ok": True, "result": {"agents": []}}}
    [gate] = gates.evaluate_checks(checks, 1500.0)
    assert "step" not in gate.as_dict()


def test_a_stepped_gate_carries_it_in_the_payload() -> None:
    [stepped] = [
        g
        for g in judge.judge_run(STEPS[:1], aggregates=AGGREGATES)
        if g.step is not None
    ][:1]
    assert stepped.as_dict()["step"] == "ramp-5"


# --------------------------------------------------------------------------
# And it reaches the page a client reads
# --------------------------------------------------------------------------


def test_the_rendered_rows_are_distinguishable() -> None:
    """The defect was found by reading the HTML, so it is checked there.

    A payload that carries the step while the table still renders seven
    identical rows would not have fixed anything.
    """
    results = _run()
    html = run_report.render_load_html(
        run_report.build_load_payload(
            run={"id": "ramp", "artifact_sha256": "a" * 64},
            tests=STEPS,
            gate_results=[g.as_dict() for g in results],
        )
    )
    body = html[html.index("<h2>Gates</h2>") :]
    rows = [r for r in body.split("<tr>") if "resource_headroom" in r]
    assert len(rows) > 1
    assert len(set(rows)) == len(rows), "identical gate rows rendered"


def test_the_establishment_row_does_not_repeat_its_own_name() -> None:
    """Its subject IS the step, so printing both would read as a stutter."""
    [establishment] = [
        g
        for g in judge.judge_run(STEPS[:1], aggregates=AGGREGATES)
        if g.gate == "call_establishment"
    ]
    html = run_report.render_load_html(
        run_report.build_load_payload(
            run={"id": "ramp", "artifact_sha256": "a" * 64},
            tests=STEPS[:1],
            gate_results=[establishment.as_dict()],
        )
    )
    body = html[html.index("<h2>Gates</h2>") :]
    [row] = [r for r in body.split("<tr>") if "call_establishment" in r]
    assert row.count("ramp-5") == 1
