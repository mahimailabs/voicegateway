"""One scope exclusion, ``pps``, and it is permanent.

**WHAT CHANGED, and why every assertion in this file moved.** This file used to
pin RTP ports and network as the two scope exclusions, on the reasoning that
nothing in this system could measure either on any node in any run. That is no
longer true, and it is not true because the coverage improved rather than
because the standard dropped:

* RTP ports are measured from ``media_ports_in_use`` / ``media_ports_total``.
* Network is measured from ``network_receive_bytes_total`` /
  ``network_transmit_bytes_total`` against an operator-DECLARED baseline, and it
  is TWO resources now (``network_in`` and ``network_out``), because a cloud
  instance meters each direction against a separate credit bucket.

So the exclusion machinery, which derives an exclusion from nothing publishing
the columns, lifted both by itself. That is exactly what it was built to do, and
:func:`test_the_exclusion_follows_from_the_columns_not_from_a_name_list` is the
test that made it self-healing.

What is left is ``pps``, and it is a PERMANENT exclusion of a different kind.
There is no per-instance-type packets-per-second allowance published by anyone,
including AWS, under any API or document: there is a numerator and no
denominator anywhere at any price. Wiring an exporter does not lift it, so it
lives in :data:`judge.PERMANENT_HEADROOM_EXCLUSIONS` rather than being derived
from a column, and its reason has to say the denominator is UNPUBLISHED rather
than uncollected. Filing it as uncollected would put it in the queue of things
somebody could fix.

**The compensating disclosure is still enforced, not trusted.** Removing rows
can move a verdict off UNKNOWN, because UNKNOWN outranks PASS, so a shrinking
exclusion set is the one change that could make a headline read better without
any underlying result changing. The tests below therefore assert both halves
every time: what is no longer excluded is now MEASURED, and what is still
excluded is still disclosed with its reason in the payload and in the HTML.
"""

from __future__ import annotations

import pytest

from voicegateway.livekit_diag import gates, run_report
from voicegateway.loadtest import judge
from voicegateway.middleware.node_samples_worker_middleware import (
    any_source_publishes,
)

HEALTHY = {"name": "ramp-20", "attempted_calls": 100, "succeeded_calls": 100}


def _payload(**kw):
    return run_report.build_load_payload(
        run={"id": "ramp-500", "artifact_sha256": "a" * 64},
        tests=[],
        scope_exclusions=judge.excluded_headroom_resources(),
        **kw,
    )


# --------------------------------------------------------------------------
# Derived, not listed
# --------------------------------------------------------------------------


def test_pps_is_the_only_resource_excluded_today() -> None:
    """Exactly one, and it is the permanent one.

    The old assertion named RTP ports and network. Both are measured now, so
    this asserts the new exact set AND, in the same breath, that the two that
    left the set left it by becoming measurable rather than by being dropped.
    An exclusion set that shrinks for any other reason is a disclosure that
    shrank.
    """
    excluded = judge.excluded_headroom_resources()
    assert sorted(excluded) == [gates.HEADROOM_PPS]
    # It is permanent, not derived: no column anywhere lifts it.
    assert sorted(judge.PERMANENT_HEADROOM_EXCLUSIONS) == [gates.HEADROOM_PPS]
    assert gates.HEADROOM_PPS not in judge.HEADROOM_REQUIREMENTS

    # THE COMPANION. The three that stopped being exclusions are not merely
    # absent from the set: something measures each of them now.
    for resource in (
        gates.HEADROOM_RTP_PORTS,
        gates.HEADROOM_NETWORK_IN,
        gates.HEADROOM_NETWORK_OUT,
    ):
        assert resource not in excluded, resource
        assert any_source_publishes(*judge.HEADROOM_REQUIREMENTS[resource]), resource


def test_file_descriptors_are_not_excluded() -> None:
    """Non-vacuous. Something CAN measure them, so they stay a real gate."""
    assert gates.HEADROOM_FILE_DESCRIPTORS not in judge.excluded_headroom_resources()
    assert any_source_publishes(
        *judge.HEADROOM_REQUIREMENTS[gates.HEADROOM_FILE_DESCRIPTORS]
    )


def test_the_exclusion_follows_from_the_columns_not_from_a_name_list() -> None:
    """The trap. Wiring an exporter must lift the exclusion by itself.

    Simulated by asking the same question about a resource whose columns ARE
    published: the machinery answers differently without anybody editing a list
    of excluded names.
    """
    for resource, columns in judge.HEADROOM_REQUIREMENTS.items():
        excluded = resource in judge.excluded_headroom_resources()
        assert excluded is not any_source_publishes(*columns), resource


def test_the_derivation_still_bites_when_a_column_is_unpublished(monkeypatch) -> None:
    """Non-vacuous, now that every derived requirement IS published.

    The loop above once had four resources of which three were excluded, so it
    exercised both directions on real data. All four are measured today, which
    would leave the machinery asserted only in its permissive direction: a
    broken ``excluded_headroom_resources`` that returned the permanent set and
    nothing else would pass it. So one requirement is pointed at a column no
    exporter publishes and the exclusion has to come back, with the column named
    so a reader can see what would close it.
    """
    monkeypatch.setitem(
        judge.HEADROOM_REQUIREMENTS,
        gates.HEADROOM_RTP_PORTS,
        ("a_column_no_exporter_publishes",),
    )
    excluded = judge.excluded_headroom_resources()
    assert sorted(excluded) == [gates.HEADROOM_PPS, gates.HEADROOM_RTP_PORTS]
    assert "a_column_no_exporter_publishes" in excluded[gates.HEADROOM_RTP_PORTS]
    assert "not evaluated rather than passing" in excluded[gates.HEADROOM_RTP_PORTS]


def test_the_permanent_reason_says_unpublished_not_uncollected() -> None:
    """A reader has to be able to see that nothing would close it.

    The old test asserted that each reason named the COLUMN that was missing,
    because each exclusion was then a gap an exporter could fill. That is the
    wrong shape of statement for the one exclusion that is left: pps has no
    column to name. The denominator is not uncollected, it is UNPUBLISHED, by
    everyone including AWS, so there is nothing to wire and no work to file.
    Saying "no exporter publishes it" here would read as a to-do.
    """
    reason = judge.excluded_headroom_resources()[gates.HEADROOM_PPS]
    assert "no denominator" in reason
    assert "no per-instance-type PPS allowance is published" in reason
    assert "not a gap awaiting work" in reason
    # And the honest other half: the EVENT is still detected, so the exclusion
    # is a boundary on what can be quantified rather than a blind spot.
    assert "network_allowance" in reason
    assert gates.NETWORK_ALLOWANCE_GATE in gates.ALL_GATES
    # A count of allowance events, deliberately NOT a ratio: rendering 9613 as
    # "961300%" is the misstatement RATIO_GATES exists to prevent.
    assert gates.NETWORK_ALLOWANCE_GATE not in gates.RATIO_GATES


# --------------------------------------------------------------------------
# pps is a fleet row; the measurable three are per-node rows again
# --------------------------------------------------------------------------


def test_the_measurable_resources_are_per_node_rows_again() -> None:
    """They stopped being a run-level fact the moment they became measurable.

    The old test asserted the opposite, that no per-node ``rtp_ports`` or
    ``network`` row may exist anywhere, because nothing could measure either on
    any node so a per-node row was the same non-fact repeated. Both halves are
    checked here in the new world, and the pair is the point:

    * a test with NOTHING correlated still fabricates no row, so an absent
      measurement never turns into an invented one, and
    * a test whose window carried the readings gets a row PER NODE, which is the
      whole reason a fleet report exists: at 500 concurrent across six nodes,
      knowing WHICH node ran out of ports is the actionable content.
    """
    subjects = [r.subject or "" for r in judge.judge_test(HEALTHY)]
    for resource in (
        gates.HEADROOM_RTP_PORTS,
        gates.HEADROOM_NETWORK_IN,
        gates.HEADROOM_NETWORK_OUT,
    ):
        assert not [s for s in subjects if s.endswith(f"/{resource}")], resource

    measured = judge.judge_test(
        HEALTHY,
        aggregate=_aggregate_with_readings(),
        network_baselines={"sip-1": {"in_bps": 1_000_000.0, "out_bps": 1_000_000.0}},
    )
    by_subject = {r.subject: r for r in measured}
    assert by_subject["sip-1/node-exporter/rtp_ports"].status == gates.PASS
    assert by_subject["sip-1/network_in"].status == gates.PASS
    assert by_subject["sip-1/network_out"].status == gates.PASS
    # Two directions, two subjects, two answers. One combined "network" row
    # would have to pick a direction, and the credit buckets are separate.
    assert by_subject["sip-1/network_in"] != by_subject["sip-1/network_out"]


def _aggregate_with_readings():
    """One node whose window carried media ports and throughput in both
    directions."""
    from voicegateway.loadtest.aggregation import TestAggregate
    from voicegateway.repository.node_correlation_repository import window_of

    return TestAggregate(
        window=window_of(1_785_661_201_000, 1_785_661_260_000),
        peak_cpu_utilisation=0.4,
        peak_memory_utilisation=0.4,
        node_samples_in_window=2,
        rtp_port_readings=[
            gates.HeadroomReading(
                node="sip-1",
                source="node-exporter",
                resource=gates.HEADROOM_RTP_PORTS,
                used=1200.0,
                limit=10001.0,
            )
        ],
        bandwidth_peaks={("sip-1", "in"): 400_000.0, ("sip-1", "out"): 500_000.0},
    )


def test_exactly_one_fleet_row_for_the_permanent_exclusion_in_a_multi_step_run() -> (
    None
):
    """Once per RUN, not once per node per test.

    A three-step ramp across three sources emitted eighteen identical rows. Two
    of the three resources behind those rows are measured now, so the run-level
    tail is ONE row, ``fleet/pps``, and it stays a gate so a written waiver has
    something to attach to. The old assertion looped over rtp_ports and network
    and demanded a fleet row for each; demanding one now would be demanding an
    exclusion for a resource that is measured.
    """
    run = [
        {"name": f"ramp-{n}", "attempted_calls": 100, "succeeded_calls": 100}
        for n in (5, 10, 20)
    ]
    results = judge.judge_run(run)
    rows = [
        r
        for r in results
        if (r.subject or "").endswith(f"/{gates.HEADROOM_PPS}")
    ]
    assert len(rows) == 1, [r.subject for r in rows]
    assert rows[0].subject == f"{gates.FLEET_SUBJECT}/{gates.HEADROOM_PPS}"
    assert rows[0].status == gates.UNKNOWN
    # THE COMPANION, and the distinction it turns on. The three that became
    # measurable DO still get one fleet row here, because this run scraped
    # nothing and a contracted criterion that emits no row at all reads as one
    # nobody agreed to. But that row is a different claim from pps: it says the
    # series were not COLLECTED, not that the quantity is unmeasurable. Only pps
    # appears in excluded_headroom_resources, and only pps says nobody publishes
    # a denominator. Collapsing the two would let a collection regression read
    # as a permanent limit of the system, which is the whole failure mode.
    excluded = judge.excluded_headroom_resources()
    for resource in (
        gates.HEADROOM_RTP_PORTS,
        gates.HEADROOM_NETWORK_IN,
        gates.HEADROOM_NETWORK_OUT,
    ):
        assert resource not in excluded, resource
        [row] = [
            r for r in results if r.subject == f"{gates.FLEET_SUBJECT}/{resource}"
        ]
        assert row.status == gates.UNKNOWN
        assert "gap in what was collected" in row.detail, resource
        assert "no denominator" not in row.detail, resource
    # And pps reads the other way round.
    [pps_row] = [
        r
        for r in results
        if r.subject == f"{gates.FLEET_SUBJECT}/{gates.HEADROOM_PPS}"
    ]
    assert "gap in what was collected" not in pps_row.detail


def test_it_remains_a_gate_so_a_waiver_can_attach() -> None:
    """The reason this is not merely a note.

    The checklist requires a threshold nobody funded to be recorded as waived,
    in writing, with a reason. An exclusion with no gate has nothing to sign.
    The old test waived the rtp_ports row; that row is a measurement now, so the
    only thing left to sign is pps.
    """
    [pps] = [
        r
        for r in judge.unmeasurable_headroom_gates()
        if (r.subject or "").endswith(f"/{gates.HEADROOM_PPS}")
    ]
    waived = gates.waive(pps, reason="not funded for this engagement")
    assert waived.status == gates.WAIVED
    assert waived.status != gates.PASS
    assert "not funded" in waived.detail


def test_the_verdict_does_not_improve_on_its_own() -> None:
    """Collapsing eighteen rows to two must not hand anybody a free PASS.

    They are still UNKNOWN, and UNKNOWN outranks PASS, so a run reaches PASS
    only once somebody waives them in writing or an exporter measures them.
    """
    run = [{"name": "ramp-20", "attempted_calls": 100, "succeeded_calls": 100}]
    assert judge.verdict_for(judge.judge_run(run)) == gates.UNKNOWN


def test_file_descriptors_still_produce_a_gate() -> None:
    """Only the one unmeasurable resource left. The measurable ones are
    untouched."""
    results = judge.judge_test(HEALTHY)
    assert [r for r in results if (r.subject or "").endswith("/file_descriptors")]


# --------------------------------------------------------------------------
# The compensating disclosure, enforced
# --------------------------------------------------------------------------


def test_the_exclusion_reaches_the_payload_with_its_reason() -> None:
    """One key now, not two, and the reason carries a different burden.

    The old assertion required every reason to say "not evaluated rather than
    passing", which is the sentence a DERIVED exclusion needs: it is waiting on
    an exporter, so the row has to refuse to read as a pass. The permanent one
    has to carry more than that, because a reader who sees a permanently
    excluded resource will otherwise file it as work. So the reason is asserted
    to state that the denominator does not exist and that this is not a gap
    awaiting work, which is strictly more than the old phrase asserted.
    """
    payload = _payload()
    exclusions = payload["scope_exclusions"]
    assert sorted(exclusions) == [gates.HEADROOM_PPS]
    reason = exclusions[gates.HEADROOM_PPS]
    assert reason.strip()
    assert "no denominator" in reason
    assert "not a gap awaiting work" in reason


def test_the_exclusion_reaches_the_rendered_html_with_its_reason() -> None:
    html = run_report.render_load_html(_payload())
    assert "Not in scope for this report" in html
    for resource, reason in _payload()["scope_exclusions"].items():
        assert run_report._esc(reason) in html, resource


def test_a_passing_verdict_still_carries_the_exclusion() -> None:
    """The invariant the whole node turns on.

    A PASS is exactly when a reader is most likely to stop reading, and exactly
    when the removed rows would have said the criterion was not fully answered.
    Two exclusions became one because two resources became measurable, which is
    the only shrinkage that may ever happen to this banner: the disclosure may
    shrink only when the coverage grows.
    """
    passing = gates.establishment_gate(
        attempted=1000,
        succeeded=1000,
        threshold=gates.MIN_ESTABLISHMENT_RATIO,
        subject="ramp-20",
    )
    payload = _payload(gate_results=[passing.as_dict()])
    assert payload["verdict"]["status"] == gates.PASS

    assert sorted(payload["scope_exclusions"]) == [gates.HEADROOM_PPS]
    html = run_report.render_load_html(payload)
    assert "Not in scope for this report" in html
    assert "not fully covered" in html
    for reason in payload["scope_exclusions"].values():
        assert run_report._esc(reason) in html


def test_the_three_part_requirement_is_never_claimed_as_covered() -> None:
    """The criterion asks for network, RTP ports AND system limits.

    Measuring one of three and reporting a clean verdict is the failure this
    node must not create, so the report says so in the same breath as the
    verdict.
    """
    html = run_report.render_load_html(_payload())
    assert "network, RTP ports and system limits" in html
    assert "not fully covered" in html


def test_the_exclusions_sit_directly_under_the_verdict() -> None:
    """Placement is the disclosure. A footnote would be a downgrade."""
    passing = gates.establishment_gate(
        attempted=10, succeeded=10, threshold=gates.MIN_ESTABLISHMENT_RATIO
    )
    html = run_report.render_load_html(_payload(gate_results=[passing.as_dict()]))
    verdict_at = html.index('class="verdict')
    exclusions_at = html.index("Not in scope for this report")
    gates_at = html.index("<h2>Gates</h2>")
    assert verdict_at < exclusions_at < gates_at


@pytest.mark.parametrize("provenance_key", ["a" * 64, None])
def test_the_exclusions_survive_either_provenance(provenance_key) -> None:
    payload = run_report.build_load_payload(
        run={"id": "r", "artifact_sha256": provenance_key},
        tests=[],
        scope_exclusions=judge.excluded_headroom_resources(),
    )
    html = run_report.render_load_html(payload)
    assert "Not in scope for this report" in html


def test_a_report_with_nothing_excluded_shows_no_block() -> None:
    """An empty banner would be noise, and would train readers to skip it."""
    html = run_report.render_load_html(
        run_report.build_load_payload(
            run={"id": "r", "artifact_sha256": "a" * 64}, tests=[], scope_exclusions={}
        )
    )
    assert "Not in scope for this report" not in html


# --------------------------------------------------------------------------
# The limits section still names them too
# --------------------------------------------------------------------------


def test_the_structural_limits_still_describe_both() -> None:
    """Belt and braces. The exclusion is the headline; the limits list is the
    detail, and dropping it there would shrink the disclosure."""
    joined = " ".join(run_report._LOAD_REPORT_LIMITS).lower()
    assert "rtp-port headroom" in joined
    assert "network headroom" in joined
