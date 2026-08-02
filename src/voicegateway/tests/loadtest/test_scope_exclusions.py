"""RTP ports and network as a scope exclusion, not eighteen gate rows.

Nothing in this system measures either, on any node, in any run. Emitting them
per node per test told a reader the same two facts eighteen times on a real
run, above the three-row table they contracted for, and a gate row implies a
measurement was attempted on that node and failed. Nothing was attempted.

**Collapsing them is only honest while the statement is harder to miss than the
rows were.** Removing eighteen UNKNOWNs can move a verdict off UNKNOWN, because
UNKNOWN outranks PASS, so this is the one change that could make a headline read
better without any underlying result changing. The tests below enforce the
compensating disclosure rather than trusting it:

* both exclusions appear in the payload AND in the rendered HTML, with reasons
* a PASS verdict still carries both, and says the criterion is not covered
* the exclusion is DERIVED from nothing being able to measure the resource, so
  wiring an exporter lifts it automatically rather than needing a second edit
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


def test_both_resources_are_excluded_today() -> None:
    excluded = judge.excluded_headroom_resources()
    assert sorted(excluded) == [gates.HEADROOM_NETWORK, gates.HEADROOM_RTP_PORTS]


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


def test_each_reason_names_the_column_that_is_missing() -> None:
    """A reader has to be able to see what would close it."""
    excluded = judge.excluded_headroom_resources()
    assert "rtp_port_range_size" in excluded[gates.HEADROOM_RTP_PORTS]
    assert "network_link_capacity_bytes" in excluded[gates.HEADROOM_NETWORK]


# --------------------------------------------------------------------------
# They are gone from the gate rows
# --------------------------------------------------------------------------


def test_no_per_node_rows_remain_for_either_resource() -> None:
    """Per TEST, they are gone entirely. They are a run-level fact."""
    subjects = [r.subject or "" for r in judge.judge_test(HEALTHY)]
    assert not [s for s in subjects if s.endswith("/rtp_ports")]
    assert not [s for s in subjects if s.endswith("/network")]


def test_exactly_one_fleet_row_per_resource_survives_a_multi_step_run() -> None:
    """Once per RUN, not once per node per test.

    A three-step ramp across three sources emitted eighteen identical rows. It
    now emits two, and they stay gates so a written waiver has something to
    attach to.
    """
    run = [
        {"name": f"ramp-{n}", "attempted_calls": 100, "succeeded_calls": 100}
        for n in (5, 10, 20)
    ]
    results = judge.judge_run(run)
    for resource in (gates.HEADROOM_RTP_PORTS, gates.HEADROOM_NETWORK):
        rows = [r for r in results if (r.subject or "").endswith(f"/{resource}")]
        assert len(rows) == 1, (resource, [r.subject for r in rows])
        assert rows[0].subject == f"{gates.FLEET_SUBJECT}/{resource}"
        assert rows[0].status == gates.UNKNOWN


def test_they_remain_gates_so_a_waiver_can_attach() -> None:
    """The reason these are not merely a note.

    The checklist requires a threshold nobody funded to be recorded as waived,
    in writing, with a reason. An exclusion with no gate has nothing to sign.
    """
    [rtp] = [
        r
        for r in judge.unmeasurable_headroom_gates()
        if (r.subject or "").endswith("/rtp_ports")
    ]
    waived = gates.waive(rtp, reason="not funded for this engagement")
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
    """Only the two unmeasurable ones left. The measurable one is untouched."""
    results = judge.judge_test(HEALTHY)
    assert [r for r in results if (r.subject or "").endswith("/file_descriptors")]


# --------------------------------------------------------------------------
# The compensating disclosure, enforced
# --------------------------------------------------------------------------


def test_both_exclusions_reach_the_payload_with_reasons() -> None:
    payload = _payload()
    exclusions = payload["scope_exclusions"]
    assert sorted(exclusions) == ["network", "rtp_ports"]
    for reason in exclusions.values():
        assert reason.strip()
        assert "not evaluated rather than passing" in reason


def test_both_exclusions_reach_the_rendered_html_with_reasons() -> None:
    html = run_report.render_load_html(_payload())
    assert "Not in scope for this report" in html
    for resource, reason in _payload()["scope_exclusions"].items():
        assert run_report._esc(reason) in html, resource


def test_a_passing_verdict_still_carries_both_exclusions() -> None:
    """The invariant the whole node turns on.

    A PASS is exactly when a reader is most likely to stop reading, and exactly
    when the removed rows would have said the criterion was not fully answered.
    """
    passing = gates.establishment_gate(
        attempted=1000,
        succeeded=1000,
        threshold=gates.MIN_ESTABLISHMENT_RATIO,
        subject="ramp-20",
    )
    payload = _payload(gate_results=[passing.as_dict()])
    assert payload["verdict"]["status"] == gates.PASS

    assert sorted(payload["scope_exclusions"]) == ["network", "rtp_ports"]
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
