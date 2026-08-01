"""The load-test report, and the one property that matters most about it.

Nothing in this build can currently produce a measured run: the load generator
does not run here, and every number in the fixtures was hand-built from schema
documentation. So a report that presented those numbers as measurements would be
the single most damaging thing this code could do, and the stamp that prevents
it is tested harder than anything else in the file.

Provenance is DERIVED, not asserted. A payload is measured only when the run it
came from carries the checksum of a real artifact, so no writer can claim
measured-ness without holding the bytes that prove it.
"""

from __future__ import annotations

import json

import pytest

from voicegateway.livekit_diag import run_report

SYNTHETIC_RUN = {
    "id": "ramp-500",
    "label": "baseline ramp",
    "project": "default",
    "tool": "gossipper",
    "tool_version": "0.1.62",
    "artifact_schema_version": "gossipper_summary_v1",
    # No checksum: built from fixtures.
    "artifact_sha256": None,
    "started_at_ms": 1_785_520_800_000,
    "ended_at_ms": 1_785_524_400_000,
}

MEASURED_RUN = {**SYNTHETIC_RUN, "artifact_sha256": "a" * 64}

TESTS = [
    {
        "name": "ramp-500",
        "sequence": 0,
        "target_concurrency": None,
        "peak_concurrency": 492,
        "started_at_ms": 1_785_520_800_000,
        "ended_at_ms": 1_785_524_400_000,
        "attempted_calls": 15000,
        "succeeded_calls": 14985,
        "failed_calls": 15,
        "failed_timeout": 3,
        "failed_unexpected_sip": 9,
        # The remaining causes are absent, not zero.
        "peak_cpu_utilisation": 0.68,
        "peak_memory_utilisation": 0.612,
        "node_samples_in_window": 12,
        "rtp_packets_sent": 88_410_000,
        "rtp_packets_received": 88_396_500,
    }
]


def _payload(run=SYNTHETIC_RUN, **kw):
    return run_report.build_load_payload(run=run, tests=TESTS, **kw)


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


def test_a_run_without_a_checksum_is_synthetic() -> None:
    payload = _payload()
    assert payload["data_provenance"] == run_report.PROVENANCE_SYNTHETIC
    assert "describes nothing that happened" in payload["provenance_basis"]


def test_a_run_carrying_a_checksum_is_measured() -> None:
    payload = _payload(MEASURED_RUN)
    assert payload["data_provenance"] == run_report.PROVENANCE_MEASURED


def test_provenance_is_top_level_not_buried() -> None:
    """A consumer must not have to go looking for it."""
    assert "data_provenance" in _payload()


def test_provenance_cannot_be_asserted_without_the_artifact() -> None:
    """There is no flag to set. Only the checksum decides.

    A run that tried to declare itself measured while carrying no checksum is
    still synthetic, because nothing reads such a declaration.
    """
    lying = {**SYNTHETIC_RUN, "data_provenance": "measured", "measured": True}
    assert _payload(lying)["data_provenance"] == run_report.PROVENANCE_SYNTHETIC


# --------------------------------------------------------------------------
# The stamp
# --------------------------------------------------------------------------


def test_a_fixture_built_payload_cannot_render_an_unstamped_file() -> None:
    """The single most important assertion in this file."""
    document = run_report.render_load_html(_payload())
    assert run_report.SYNTHETIC_STAMP in document


def test_the_stamp_is_the_first_visible_element() -> None:
    """A footer would be scrolled past. The body opens with it.

    Anyone who jumps straight to the numbers passes the banner on the way.
    """
    document = run_report.render_load_html(_payload())
    body = document[document.index("<body>") + len("<body>") :]
    assert body.lstrip().startswith('<div class="stamp">')
    # And it precedes every number in the document.
    assert body.index(run_report.SYNTHETIC_STAMP) < body.index("492")


def test_a_measured_run_is_not_stamped() -> None:
    """Non-vacuous: the stamp is conditional, not unconditional markup."""
    document = run_report.render_load_html(_payload(MEASURED_RUN))
    assert run_report.SYNTHETIC_STAMP not in document
    assert 'class="stamp"' not in document


def test_the_stamp_carries_no_customer_wording() -> None:
    """It says the file is not a deliverable, and nothing about who for.

    Scoped to the stamp block rather than the whole document on purpose: the
    inherited limits mention the LiveKit "client SDK", which is the name of a
    piece of software and not a reference to anybody's customer.
    """
    assert run_report.SYNTHETIC_STAMP == "SYNTHETIC DATA: NOT A DELIVERABLE"
    document = run_report.render_load_html(_payload())
    stamp = document[document.index('<div class="stamp">') : document.index("<h1>")]
    lowered = stamp.lower()
    for word in ("client", "customer", "engagement", "deliverable for"):
        assert word not in lowered, f"the stamp says {word!r}"


# --------------------------------------------------------------------------
# The five columns
# --------------------------------------------------------------------------


def test_every_column_the_report_owes_is_present() -> None:
    [test] = _payload()["tests"]
    for column in (
        "peak_concurrency",
        "duration_ms",
        "establishment_ratio",
        "peak_cpu_utilisation",
        "peak_memory_utilisation",
        "failed_calls",
        "failures_by_cause",
    ):
        assert column in test, column
    assert test["peak_concurrency"] == 492
    assert test["duration_ms"] == 3_600_000


def test_the_establishment_ratio_is_computed_from_the_counts() -> None:
    """Never stored beside them, so the two can never disagree."""
    [test] = _payload()["tests"]
    assert test["establishment_ratio"] == pytest.approx(14985 / 15000)


def test_a_ratio_is_none_rather_than_zero_when_nothing_was_attempted() -> None:
    payload = run_report.build_load_payload(
        run=SYNTHETIC_RUN, tests=[{"name": "t", "attempted_calls": 0}]
    )
    assert payload["tests"][0]["establishment_ratio"] is None


def test_a_cause_the_artifacts_omitted_is_absent_not_zero() -> None:
    """A 0 would claim the generator saw none of that cause."""
    [test] = _payload()["tests"]
    causes = test["failures_by_cause"]
    assert causes == {"timeout": 3, "unexpected_sip": 9}
    assert "cancelled" not in causes


def test_an_unmeasured_value_renders_as_words_never_as_zero() -> None:
    thin = run_report.build_load_payload(
        run=SYNTHETIC_RUN, tests=[{"name": "t", "peak_concurrency": None}]
    )
    document = run_report.render_load_html(thin)
    assert "not measured" in document


# --------------------------------------------------------------------------
# Capacity
# --------------------------------------------------------------------------


def test_a_missing_capacity_figure_is_explained_not_omitted() -> None:
    """A silently absent table reads as a table nobody needed."""
    document = run_report.render_load_html(_payload())
    assert "No capacity table" in document
    assert "inventing" in document


def test_a_refused_figure_carries_the_reason_it_was_refused() -> None:
    payload = _payload(
        capacity={
            "calls_per_node": None,
            "reason": "the ramp plateaued at 124, which is the generator's ceiling",
        }
    )
    document = run_report.render_load_html(payload)
    assert "plateaued" in document


def test_a_capacity_table_renders_every_tier_with_its_spare() -> None:
    payload = _payload(
        capacity={
            "calls_per_node": 150,
            "reason": "highest concurrency sustained at or under 70% CPU",
            "tiers": [
                {
                    "target_concurrency": 500,
                    "nodes_for_load": 4,
                    "spare_nodes": 1,
                    "nodes": 5,
                }
            ],
            "instance_type": {
                "name": "c7i.2xlarge",
                "role": "SIP",
                "citation": "sizing-runbook.md:115",
            },
        }
    )
    document = run_report.render_load_html(payload)
    assert "sizing-runbook.md:115" in document
    assert "Nothing here derives a machine type" in document


# --------------------------------------------------------------------------
# Gates and limits
# --------------------------------------------------------------------------


def test_gates_are_read_never_re_judged() -> None:
    payload = _payload(
        gate_results=[
            {
                "gate": "establishment",
                "status": "WAIVED",
                "subject": "ramp-500",
                "detail": "fewer nodes were funded",
                "waiver_reason": "accepted in writing by the operator",
            }
        ]
    )
    assert payload["gates_recorded"] is True
    document = run_report.render_load_html(payload)
    assert "WAIVED" in document


def test_a_run_with_no_recorded_gates_says_so() -> None:
    payload = _payload()
    assert payload["gates_recorded"] is False
    assert payload["gates"] is None


def test_the_unmeasured_list_names_every_known_gap() -> None:
    """A limits list that goes quiet reads as a clean bill of health."""
    limits = " ".join(_payload()["not_measured"]).lower()
    for gap in ("rtp-port headroom", "network headroom", "per-call packet loss"):
        assert gap in limits, gap
    # And it inherits the shared limits rather than replacing them.
    assert "single-vantage" in limits or "one vantage point" in limits


def test_overlap_is_never_described_as_attribution() -> None:
    document = run_report.render_load_html(_payload())
    assert "time overlap" in document
    assert "never that a node served" in document


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------


def test_the_payload_is_json_serialisable() -> None:
    json.loads(json.dumps(_payload()))


def test_the_two_report_kinds_are_distinguishable() -> None:
    """A consumer must be able to tell which export it is holding."""
    assert run_report.LOAD_REPORT_KIND != run_report.REPORT_KIND
    assert _payload()["kind"] == run_report.LOAD_REPORT_KIND


def test_build_and_render_are_synchronous() -> None:
    """Awaiting either would be a bug; neither returns a coroutine."""
    import inspect

    assert not inspect.iscoroutinefunction(run_report.build_load_payload)
    assert not inspect.iscoroutinefunction(run_report.render_load_html)


def test_the_filename_cannot_smuggle_anything_into_a_header() -> None:
    assert run_report.load_report_filename("../../etc/passwd").endswith(".html")
    assert "/" not in run_report.load_report_filename("../../etc/passwd")


# --------------------------------------------------------------------------
# Reproducible-test-assets appendix
# --------------------------------------------------------------------------


def _appendix():
    return {
        "commands": [
            run_report.appendix_entry(
                label="ramp",
                detail=(
                    "gossipper sipp -sf uac-media.xml -rsa wss://media.example.com:5060 "
                    "-l 500 -r 4.1667 -m 15000 -pause_ms 118000 -trace_stat"
                ),
                citation="operator notebook, step 4.1",
            )
        ],
        "flags": [
            run_report.appendix_entry(
                label="-l",
                detail="Max concurrent calls. Defaults to 1: omitting it caps the "
                "whole run at one concurrent call.",
                citation="gossipper sipp -h, binary 0.1.62",
            )
        ],
        "toolchain": [
            run_report.appendix_entry(
                label="build",
                detail="Does not build on macOS; cross-compile for linux/amd64.",
                citation="observed on 0.1.61 and 0.1.62",
            )
        ],
    }


def test_an_uncited_appendix_entry_is_refused() -> None:
    """An uncited command is indistinguishable from an invented one."""
    with pytest.raises(ValueError):
        run_report.appendix_entry(label="ramp", detail="gossipper sipp", citation="  ")


def test_absolute_urls_are_reduced_to_a_host_label() -> None:
    """Two reasons at once: self-containment, and not leaking an endpoint."""
    assert run_report.redact_urls("wss://media.example.com/rtc") == "media.example.com"
    assert run_report.redact_urls("see http://10.0.0.4:5060/x now") == (
        "see 10.0.0.4:5060 now"
    )
    # A string with no URL is untouched.
    assert run_report.redact_urls("-l 500 -r 4.1667") == "-l 500 -r 4.1667"


def test_an_appendix_carrying_a_url_still_renders_a_self_contained_file() -> None:
    """The whole reason redaction happens before the HTML is built."""
    document = run_report.render_load_html(_payload(appendix=_appendix()))
    lowered = document.lower()
    for marker in ("http://", "https://", "wss://", "src=", "url("):
        assert marker not in lowered, f"appendix leaked {marker!r}"
    # The host survived, because a reader reproducing a run needs to know which.
    assert "media.example.com" in document


def test_the_appendix_renders_every_section_with_its_citation() -> None:
    document = run_report.render_load_html(_payload(appendix=_appendix()))
    assert "Reproducible test assets" in document
    assert "gossipper sipp -h, binary 0.1.62" in document
    assert "operator notebook, step 4.1" in document
    assert "cross-compile for linux/amd64" in document


def test_a_missing_appendix_is_stated_not_silently_omitted() -> None:
    """A section that vanishes reads as one nobody needed."""
    document = run_report.render_load_html(_payload())
    assert "None recorded" in document
    assert "cannot be reproduced by anybody else" in document


def test_scenario_files_are_referenced_never_reproduced() -> None:
    """They are somebody else's authored work, not an interface fact."""
    document = run_report.render_load_html(_payload(appendix=_appendix()))
    assert "referenced by name rather than reproduced" in document


def test_the_appendix_travels_in_the_payload_not_only_the_html() -> None:
    """An automated consumer inherits the provenance with the commands."""
    payload = _payload(appendix=_appendix())
    assert payload["appendix"]["commands"][0]["citation"]
    json.loads(json.dumps(payload))
