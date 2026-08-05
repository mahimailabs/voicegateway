"""The load-generator artifact parser, and the four ways it can lie quietly.

Every trap here produces a PLAUSIBLE NUMBER rather than an error, which is why
each one gets a test that pins the right answer next to the wrong one it would
otherwise have produced:

1. Peak concurrency taken from ``summary.json`` reads 0 for a busy run, because
   the summary's ``active_calls`` is the end-of-run drain state.
2. Establishment scanned per-row over the CSV reads 0, because the first
   interval has calls in flight and none completed.
3. ``health.passed`` is the generator's verdict against its own threshold.
   Echoing it makes the gate layer a passthrough, and the passthrough is
   invisible because the numbers agree.
4. A cause the artifact omitted, rendered as 0, claims there were none.

The fixture values are inline rather than read from disk, so the wrong answer
each trap yields is visible in the same file as the assertion that rejects it.
They are hand-built from the generator's published schema documentation and
prove only that this parser is defensive, never that it matches a real artifact.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from voicegateway.loadtest import artifacts as art

# The 43-column -trace_stat header, in the documented order.
STAT_HEADER = (
    "timestamp,elapsed_ms,total_calls,success_calls,failed_calls,active_calls,"
    "success_ratio,calls_per_second,retransmits,timeouts,avg_call_ms,"
    "call_stddev_ms,avg_invite_ms,invite_stddev_ms,rtp_packets_sent,"
    "rtp_packets_received,rtcp_sender_reports,rtcp_receiver_reports,"
    "rtcp_packets_received,failure_timeout,failure_unexpected_sip,"
    "failure_transport_error,failure_parse_error,failure_scenario_error,"
    "failure_cancelled,interval_ms,interval_calls_per_second,delta_total_calls,"
    "delta_success_calls,delta_failed_calls,delta_retransmits,delta_timeouts,"
    "delta_rtp_packets_sent,delta_rtp_packets_received,delta_rtcp_sender_reports,"
    "delta_rtcp_receiver_reports,delta_rtcp_packets_received,"
    "delta_failure_timeout,delta_failure_unexpected_sip,"
    "delta_failure_transport_error,delta_failure_parse_error,"
    "delta_failure_scenario_error,delta_failure_cancelled"
)

# Three intervals of a one-hour, 500-concurrent run. The middle row is where
# concurrency actually peaks; the last row has drained to zero.
STAT_ROWS = [
    # Early: 4 calls in flight, none completed yet, so success_ratio is 0.
    "2026-07-31T18:00:01Z,1000,4,0,0,4,0,4,0,0,0,0,0,0,1200,1180,0,0,0,"
    "0,0,0,0,0,0,1000,4,4,0,0,0,0,1200,1180,0,0,0,0,0,0,0,0,0",
    # Mid-run: 492 concurrent. THIS is the run's peak concurrency.
    "2026-07-31T18:30:00Z,1800000,7500,7002,6,492,0.9991,4.1667,5,1,118000,40,"
    "300,20,44200000,44190000,14985,14979,29964,1,4,1,0,0,0,1000,4.1,7496,7002,"
    "6,5,1,44198800,44188820,14985,14979,29964,1,4,1,0,0,0",
    # Drained: active_calls back to 0. Cumulative columns are final.
    "2026-07-31T19:00:00Z,3600000,15000,14985,15,0,0.999,4.1667,12,3,118042,41,"
    "310,21,88410000,88396500,29970,29958,59928,3,9,2,0,1,0,1000,4.1,7500,7983,"
    "9,7,2,44210000,44206500,14985,14979,29964,2,5,1,0,1,0",
]

SUMMARY: dict = {
    "schema_version": "gossipper_summary_v1",
    "tool_version": "gossipper 0.1.62",
    "started_at": "2026-07-31T18:00:00Z",
    "finished_at": "2026-07-31T19:00:00Z",
    "duration": "1h0m0s",
    "elapsed_ms": 3600000,
    "total_calls": 15000,
    "success_calls": 14985,
    "failed_calls": 15,
    # The drain value. Sourcing concurrency from here reports 0 for this run.
    "active_calls": 0,
    "success_ratio": 0.999,
    "calls_per_second": 4.1667,
    "retransmits": 12,
    "timeouts": 3,
    "failure_classes": {
        "timeout": 3,
        "unexpected_sip": 9,
        "transport_error": 2,
        "parse_error": 0,
        "scenario_error": 1,
        "cancelled": 0,
    },
    "rtd": {
        "answer": {
            "avg": 412.6,
            "min": 188,
            "max": 2140,
            "last": 397,
            "stddev": 143.8,
            "buckets": {"<=200": 41, "<=500": 13802, "<=1000": 1096, ">2000": 1},
        }
    },
    "media": {
        "rtp_packets_sent": 88410000,
        "rtp_packets_received": 88396500,
    },
    # Never read. See test_the_generators_own_verdict_is_not_read.
    "health": {"passed": True, "min_success_ratio": 0.995},
    "findings": [],
}


def _write(
    directory: Path, *, summary: dict | None = SUMMARY, csv: bool = True
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    if summary is not None:
        (directory / "summary.json").write_text(json.dumps(summary))
    if csv:
        (directory / "run_stat.csv").write_text(
            STAT_HEADER + "\n" + "\n".join(STAT_ROWS) + "\n"
        )
    return directory


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    return _write(tmp_path / "ramp-500")


# --------------------------------------------------------------------------
# Trap 1: concurrency
# --------------------------------------------------------------------------


def test_peak_concurrency_comes_from_the_csv_not_the_summary(run_dir: Path) -> None:
    """The whole reason the CSV is a required surface.

    The summary's active_calls is 0 because the run had drained by the time it
    was written. A parser that read concurrency from there would report that a
    500-concurrent run held no calls at all.
    """
    parsed = art.parse_test_directory(run_dir)
    assert parsed.peak_concurrency == 492
    # The wrong answer, pinned so this test cannot pass by coincidence.
    assert SUMMARY["active_calls"] == 0
    assert parsed.peak_concurrency != SUMMARY["active_calls"]


def test_concurrency_is_none_when_there_is_no_csv(tmp_path: Path) -> None:
    """Not zero. The summary alone cannot answer this question at all.

    Falling back to the summary's drain value here is exactly the bug; None
    forces the caller to render it as not-measured.
    """
    parsed = art.parse_test_directory(_write(tmp_path / "only-summary", csv=False))
    assert parsed.peak_concurrency is None
    # Everything the summary CAN answer still came through.
    assert parsed.attempted_calls == 15000


# --------------------------------------------------------------------------
# Trap 2: establishment
# --------------------------------------------------------------------------


def test_establishment_is_computed_from_counts_not_scanned_per_row(
    run_dir: Path,
) -> None:
    """A per-row min over success_ratio reads 0 and fails a healthy run."""
    parsed = art.parse_test_directory(run_dir)
    assert parsed.establishment_ratio == pytest.approx(14985 / 15000)
    assert parsed.establishment_ratio > 0.995

    # The wrong answer, pinned: the first interval legitimately reads 0.
    per_row_min = min(
        s.success_ratio for s in parsed.samples if s.success_ratio is not None
    )
    assert per_row_min == 0.0
    assert parsed.establishment_ratio != per_row_min


def test_establishment_is_none_rather_than_zero_when_nothing_was_attempted() -> None:
    """A run that placed no calls has no establishment rate.

    0.0 would fail the 0.995 gate for a run that never ran, which is a different
    finding from a run that ran badly.
    """
    empty = art.ParsedTest(name="t", attempted_calls=0, succeeded_calls=0)
    assert empty.establishment_ratio is None
    unmeasured = art.ParsedTest(name="t")
    assert unmeasured.establishment_ratio is None


def test_the_reported_ratio_is_kept_only_to_contradict_the_counts(
    run_dir: Path,
) -> None:
    """The generator's own ratio is a cross-check, never the verdict."""
    parsed = art.parse_test_directory(run_dir)
    assert parsed.reported_success_ratio == 0.999
    # Rounding is agreement: 14985/15000 is 0.999 exactly.
    assert parsed.reported_ratio_disagrees is False

    # A summary whose ratio contradicts its own counts is caught.
    lying = replace(parsed, reported_success_ratio=0.60)
    assert lying.reported_ratio_disagrees is True

    # And a missing ratio is not a disagreement.
    silent = replace(parsed, reported_success_ratio=None)
    assert silent.reported_ratio_disagrees is False


# --------------------------------------------------------------------------
# Trap 3: the generator's self-assessment
# --------------------------------------------------------------------------


def test_the_generators_own_verdict_is_not_read(tmp_path: Path) -> None:
    """Two summaries differing ONLY in health.passed must parse identically.

    This is the strong form of the check. Asserting the module's source never
    mentions ``passed`` would fail on the docstring that explains why it is
    ignored; asserting the parsed output is byte-identical proves the verdict
    reached nothing.
    """
    honest = _write(tmp_path / "a", summary=SUMMARY)
    failing = dict(SUMMARY)
    failing["health"] = {"passed": False, "min_success_ratio": 0.995}
    flipped = _write(tmp_path / "b", summary=failing)

    a = art.parse_test_directory(honest, name="same")
    b = art.parse_test_directory(flipped, name="same")
    assert a == b, "health.passed changed the parse, so the verdict is being read"


def test_nothing_parsed_carries_a_pass_fail_verdict(run_dir: Path) -> None:
    """The parser reports measurements. Judging them happens in gates.py."""
    parsed = art.parse_test_directory(run_dir)
    names = set(vars(parsed))
    assert not {"passed", "health", "verdict", "status"} & names, (
        f"the parsed result carries a verdict field: {sorted(names)}"
    )


# --------------------------------------------------------------------------
# Trap 4: failures, and the two shapes they arrive in
# --------------------------------------------------------------------------


def test_both_surfaces_normalise_onto_the_same_cause_names(run_dir: Path) -> None:
    """The summary NESTS failures; the CSV carries them FLAT.

    The README's ``failure_*`` wording matches the CSV, not the JSON, so a
    parser that assumed one naming would read nothing from the other surface.
    """
    from_json = art.parse_summary(run_dir / "summary.json").failures_by_cause
    from_csv = art.parse_stat_csv(run_dir / "run_stat.csv")[-1].failures_by_cause
    assert from_json == {
        "timeout": 3,
        "unexpected_sip": 9,
        "transport_error": 2,
        "parse_error": 0,
        "scenario_error": 1,
        "cancelled": 0,
    }
    assert from_csv == from_json


def test_an_omitted_cause_is_absent_not_zero(tmp_path: Path) -> None:
    """A 0 would claim there were none; absence says the artifact did not say."""
    partial = dict(SUMMARY)
    partial["failure_classes"] = {"timeout": 3}
    parsed = art.parse_test_directory(_write(tmp_path / "p", summary=partial))
    assert parsed.failures_by_cause == {"timeout": 3}
    assert "cancelled" not in parsed.failures_by_cause
    # But a genuine zero survives, because 0 and absent are different claims.
    assert art.parse_test_directory(tmp_path / "p").failures_by_cause["timeout"] == 3


def test_the_per_interval_deltas_are_not_summed_into_the_totals(
    run_dir: Path,
) -> None:
    """delta_failure_* is per-interval; failure_* beside it is cumulative.

    Reading the delta column as the total under-reports every interval after the
    first, and the result still looks like a believable failure count. In the
    last row the two genuinely differ: 9 cumulative against a 5 delta.
    """
    rows = art.parse_stat_csv(run_dir / "run_stat.csv")
    assert rows[-1].failures_by_cause["unexpected_sip"] == 9
    # The wrong answer, pinned: the delta column in that same row reads 5.
    assert rows[-1].failures_by_cause["unexpected_sip"] != 5
    # And no delta column leaked in under its own name.
    assert not [k for k in rows[-1].failures_by_cause if k.startswith("delta")]


# --------------------------------------------------------------------------
# The defensive contract: a named error on a shape this parser does not know
# --------------------------------------------------------------------------


def test_an_unknown_summary_schema_raises_a_named_error(tmp_path: Path) -> None:
    bumped = dict(SUMMARY)
    bumped["schema_version"] = "gossipper_summary_v2"
    directory = _write(tmp_path / "v2", summary=bumped)
    with pytest.raises(art.UnknownArtifactSchema) as excinfo:
        art.parse_test_directory(directory)
    # The message must name what it found and what it wanted, or a human cannot
    # act on it.
    assert "gossipper_summary_v2" in str(excinfo.value)
    assert art.SUPPORTED_SUMMARY_SCHEMA in str(excinfo.value)


def test_a_summary_with_no_schema_version_is_refused(tmp_path: Path) -> None:
    """Not read on a best-effort basis. An unversioned file is an unknown one."""
    anonymous = {k: v for k, v in SUMMARY.items() if k != "schema_version"}
    with pytest.raises(art.UnknownArtifactSchema):
        art.parse_test_directory(_write(tmp_path / "anon", summary=anonymous))


def test_a_csv_missing_a_required_column_raises_a_named_error(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "short"
    directory.mkdir()
    (directory / "s.csv").write_text("timestamp,total_calls\n2026-07-31T18:00:00Z,4\n")
    with pytest.raises(art.UnknownArtifactSchema) as excinfo:
        art.parse_stat_csv(directory / "s.csv")
    assert "active_calls" in str(excinfo.value)


def test_columns_are_recognised_by_name_not_position(tmp_path: Path) -> None:
    """A generator that adds or reorders a column must still import."""
    directory = tmp_path / "reordered"
    directory.mkdir()
    (directory / "s.csv").write_text(
        "brand_new_column,active_calls,failed_calls,success_calls,total_calls,"
        "elapsed_ms\nxyz,492,6,7002,7500,1800000\n"
    )
    [row] = art.parse_stat_csv(directory / "s.csv")
    assert row.active_calls == 492
    assert row.total_calls == 7500


def test_a_present_but_unreadable_value_is_malformed_not_missing(
    tmp_path: Path,
) -> None:
    """Distinct errors: an unknown VERSION and a corrupt FILE warrant different
    responses, and silently returning None would turn the second into the
    first."""
    broken = dict(SUMMARY)
    broken["total_calls"] = "not-a-number"
    with pytest.raises(art.MalformedArtifact):
        art.parse_test_directory(_write(tmp_path / "bad", summary=broken))

    directory = tmp_path / "badjson"
    directory.mkdir()
    (directory / "summary.json").write_text("{not json")
    with pytest.raises(art.MalformedArtifact):
        art.parse_summary(directory / "summary.json")


def test_an_empty_value_is_not_measured_rather_than_zero(tmp_path: Path) -> None:
    directory = tmp_path / "gappy"
    directory.mkdir()
    (directory / "s.csv").write_text(
        "elapsed_ms,total_calls,success_calls,failed_calls,active_calls\n1000,4,,,\n"
    )
    [row] = art.parse_stat_csv(directory / "s.csv")
    assert row.total_calls == 4
    assert row.success_calls is None
    assert row.active_calls is None


# --------------------------------------------------------------------------
# calls.jsonl: optional enrichment, never a requirement
# --------------------------------------------------------------------------


def test_an_import_succeeds_without_call_records(run_dir: Path) -> None:
    """Its schema is documented nowhere, so it can never be required."""
    parsed = art.parse_test_directory(run_dir)
    assert parsed.call_records_status == "absent"
    assert parsed.call_records_count is None
    assert parsed.attempted_calls == 15000


def test_call_records_are_counted_but_not_interpreted(run_dir: Path) -> None:
    (run_dir / "calls.jsonl").write_text(
        '{"whatever": 1}\n{"unknown_shape": true}\n\n{"third": null}\n'
    )
    parsed = art.parse_test_directory(run_dir)
    assert parsed.call_records_status == "present"
    assert parsed.call_records_count == 3
    # No field mapping was applied, so nothing from those records reached the
    # measured columns.
    assert parsed.attempted_calls == 15000


def test_unreadable_call_records_never_fail_the_import(run_dir: Path) -> None:
    (run_dir / "calls.jsonl").write_text("{not json at all\n")
    parsed = art.parse_test_directory(run_dir)
    assert parsed.call_records_status == "unreadable"
    assert parsed.call_records_count is None
    # The primary surfaces still imported.
    assert parsed.peak_concurrency == 492


# --------------------------------------------------------------------------
# Either surface alone, and neither
# --------------------------------------------------------------------------


def test_the_csv_alone_is_a_usable_import(tmp_path: Path) -> None:
    directory = _write(tmp_path / "csv-only", summary=None)
    parsed = art.parse_test_directory(directory)
    assert parsed.peak_concurrency == 492
    # Cumulative columns fall back to the LAST row safely; active_calls does not.
    assert parsed.attempted_calls == 15000
    assert parsed.succeeded_calls == 14985
    assert parsed.duration_ms == 3600000
    # Nothing only the summary carries was invented.
    assert parsed.answer_latency is None
    assert parsed.artifact_schema_version is None


def test_neither_surface_present_raises_missing(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(art.MissingArtifact):
        art.parse_test_directory(empty)


# --------------------------------------------------------------------------
# The rest of the mapping
# --------------------------------------------------------------------------


def test_answer_latency_is_carried_through_without_inventing_percentiles(
    run_dir: Path,
) -> None:
    """rtd.answer is INVITE to 200 OK, the sipp_rtd source and the top rung.

    The buckets are kept with the generator's own labels. Deriving a p95 from
    bucket edges would fabricate precision the artifact does not carry.
    """
    latency = art.parse_test_directory(run_dir).answer_latency
    assert latency is not None
    assert latency.avg_ms == 412.6
    assert latency.max_ms == 2140
    assert latency.buckets["<=500"] == 13802
    assert not hasattr(latency, "p95_ms")


def test_a_run_without_answer_timing_has_no_latency(tmp_path: Path) -> None:
    """Absent, not zero. The scenario simply did not configure start_rtd."""
    no_rtd = {k: v for k, v in SUMMARY.items() if k != "rtd"}
    parsed = art.parse_test_directory(_write(tmp_path / "nortd", summary=no_rtd))
    assert parsed.answer_latency is None


def test_timestamps_become_epoch_milliseconds_in_utc(run_dir: Path) -> None:
    parsed = art.parse_test_directory(run_dir)
    # 2026-07-31T18:00:00Z
    assert parsed.started_at_ms == 1785520800000
    assert parsed.ended_at_ms == parsed.started_at_ms + 3_600_000


def test_the_tool_and_its_version_are_split_without_inventing_either(
    run_dir: Path,
) -> None:
    parsed = art.parse_test_directory(run_dir)
    assert parsed.tool == "gossipper"
    assert parsed.tool_version == "0.1.62"


def test_an_unspaced_tool_string_keeps_the_whole_value(tmp_path: Path) -> None:
    """Nothing is invented for a differently-formatted build string."""
    odd = dict(SUMMARY)
    odd["tool_version"] = "gossipper-0.1.62"
    parsed = art.parse_test_directory(_write(tmp_path / "odd", summary=odd))
    assert parsed.tool == "gossipper-0.1.62"
    assert parsed.tool_version is None


def test_rtp_counts_are_carried_as_per_test_aggregates(run_dir: Path) -> None:
    parsed = art.parse_test_directory(run_dir)
    assert parsed.rtp_packets_sent == 88410000
    assert parsed.rtp_packets_received == 88396500


def test_the_name_defaults_to_the_directory_but_can_be_overridden(
    run_dir: Path,
) -> None:
    assert art.parse_test_directory(run_dir).name == "ramp-500"
    assert art.parse_test_directory(run_dir, name="step-3").name == "step-3"


def test_every_parsed_field_maps_onto_a_load_run_test_column(run_dir: Path) -> None:
    """DATA3 writes this straight into load_run_tests, so the names must line up.

    A mismatch here surfaces as an unexpected keyword at import time rather than
    as a wrong number, but only if something checks.
    """
    from voicegateway.repository.load_runs_repository import LoadRunTestInput

    parsed = art.parse_test_directory(run_dir)
    columns = set(LoadRunTestInput.__dataclass_fields__)
    for name in (
        "started_at_ms",
        "ended_at_ms",
        "peak_concurrency",
        "attempted_calls",
        "succeeded_calls",
        "failed_calls",
        "rtp_packets_sent",
        "rtp_packets_received",
    ):
        assert name in columns, f"{name} has no column on load_run_tests"
        assert hasattr(parsed, name)


# --------------------------------------------------------------------------
# Regressions found by adversarially probing the error paths
# --------------------------------------------------------------------------


@pytest.mark.parametrize("token", ["nan", "NaN", "inf", "-inf", "Infinity"])
def test_a_nonfinite_count_is_a_named_error_not_a_crash(
    tmp_path: Path, token: str
) -> None:
    """float() accepts these; int() then raises outside this module's hierarchy.

    ``float("nan")`` and ``float("Infinity")`` both parse cleanly, so the
    failure only appears at the later ``int()``, as a bare ValueError or
    OverflowError. A caller catching ArtifactError would not catch either.
    """
    directory = tmp_path / f"nf-{token}"
    directory.mkdir()
    (directory / "s.csv").write_text(
        "elapsed_ms,total_calls,success_calls,failed_calls,active_calls\n"
        f"1000,{token},0,0,4\n"
    )
    with pytest.raises(art.ArtifactError):
        art.parse_stat_csv(directory / "s.csv")


def test_a_nonfinite_value_in_json_is_a_named_error(tmp_path: Path) -> None:
    """json.loads accepts the bare NaN and Infinity tokens by default."""
    directory = tmp_path / "nfjson"
    directory.mkdir()
    (directory / "summary.json").write_text(
        '{"schema_version": "gossipper_summary_v1", "total_calls": Infinity}'
    )
    with pytest.raises(art.MalformedArtifact):
        art.parse_summary(directory / "summary.json")


def test_a_nan_ratio_cannot_silently_defeat_the_cross_check(tmp_path: Path) -> None:
    """Every comparison against NaN is False, including the disagreement test.

    Storing a NaN would make reported_ratio_disagrees answer "they agree" for
    precisely the corrupt input the cross-check exists to catch.
    """
    poisoned = dict(SUMMARY)
    poisoned["success_ratio"] = float("nan")
    directory = tmp_path / "nanratio"
    directory.mkdir()
    (directory / "summary.json").write_text(
        json.dumps(poisoned)  # json.dumps writes a bare NaN token
    )
    with pytest.raises(art.MalformedArtifact):
        art.parse_summary(directory / "summary.json")

    # And the property itself would indeed have been fooled, which is why the
    # value is refused at the boundary rather than handled downstream.
    fooled = art.ParsedTest(
        name="t",
        attempted_calls=100,
        succeeded_calls=10,
        reported_success_ratio=float("nan"),
    )
    assert fooled.reported_ratio_disagrees is False


def test_a_summary_that_omits_failure_classes_does_not_take_the_csv_totals(
    tmp_path: Path,
) -> None:
    """An absent section is not the same fact as an empty one.

    A clean run may omit failure_classes entirely. Treating that as "no answer"
    let the CSV's cumulative counts overwrite it, producing failures that
    contradict the summary's own failed_calls.
    """
    clean = {k: v for k, v in SUMMARY.items() if k != "failure_classes"}
    clean["failed_calls"] = 0
    clean["success_calls"] = 15000
    directory = tmp_path / "clean"
    directory.mkdir()
    (directory / "summary.json").write_text(json.dumps(clean))
    # A companion CSV whose cumulative column disagrees with the summary.
    (directory / "s.csv").write_text(
        "elapsed_ms,total_calls,success_calls,failed_calls,active_calls,"
        "failure_timeout\n3600000,15000,15000,0,0,5\n"
    )
    parsed = art.parse_test_directory(directory)
    assert parsed.failed_calls == 0
    total_by_cause = sum(parsed.failures_by_cause.values())
    assert total_by_cause == 0, (
        f"the CSV's failure counts overwrote a summary that reported a clean "
        f"run: failed_calls=0 but causes sum to {total_by_cause}"
    )


def test_the_csv_still_supplies_failures_when_the_summary_is_absent(
    tmp_path: Path,
) -> None:
    """The fallback must still work; only its trigger condition changed."""
    parsed = art.parse_test_directory(_write(tmp_path / "csvonly", summary=None))
    assert parsed.failures_by_cause["unexpected_sip"] == 9


def test_a_byte_order_mark_does_not_silently_void_every_timestamp(
    tmp_path: Path,
) -> None:
    """A BOM attaches to the first header field and timestamp is not required.

    So nothing raises, and every at_ms reads None while the column sits there
    full of valid data.
    """
    directory = tmp_path / "bom"
    directory.mkdir()
    (directory / "s.csv").write_bytes(
        b"\xef\xbb\xbf"
        + (
            b"timestamp,elapsed_ms,total_calls,success_calls,failed_calls,"
            b"active_calls\n2026-07-31T18:00:01Z,1000,4,0,0,4\n"
        )
    )
    [row] = art.parse_stat_csv(directory / "s.csv")
    assert row.at_ms is not None, "the BOM voided the timestamp column"
    assert row.active_calls == 4


def test_a_repeated_column_is_refused_rather_than_silently_collapsed(
    tmp_path: Path,
) -> None:
    """DictReader keeps the last occurrence, so the value read is unknowable."""
    directory = tmp_path / "dup"
    directory.mkdir()
    (directory / "s.csv").write_text(
        "elapsed_ms,total_calls,total_calls,success_calls,failed_calls,"
        "active_calls\n1000,4,999,0,0,4\n"
    )
    with pytest.raises(art.UnknownArtifactSchema) as excinfo:
        art.parse_stat_csv(directory / "s.csv")
    assert "total_calls" in str(excinfo.value)


def _call_record(number: int, *, sent: int, received: int, success: bool = True) -> str:
    """One record in the shape a captured gossipper run actually writes."""
    return json.dumps(
        {
            "schema_version": "gossipper_call_record_v1",
            "call_id": f"gossip-{number}-{number}-3e3d7839",
            "call_number": number,
            "success": success,
            "duration_ms": 300277,
            "media": {
                "RTPPacketsSent": sent,
                "RTPOctetsSent": sent * 160,
                "RTPPacketsReceived": received,
                "RTCPSenderReports": 599,
            },
        }
    )


class TestCallRecordMedia:
    """Per-call inbound media, which no run total can reconstruct."""

    def _write(self, tmp_path: Path, lines: list[str]) -> art.ParsedTest:
        (tmp_path / "calls.jsonl").write_text("\n".join(lines) + "\n")
        (tmp_path / "summary.json").write_text(
            json.dumps(
                {
                    "schema_version": "gossipper_summary_v1",
                    "total_calls": len(lines),
                    "success_calls": len(lines),
                    "failed_calls": 0,
                }
            )
        )
        return art.parse_test_directory(tmp_path)

    def test_counts_calls_that_got_nothing_back(self, tmp_path: Path) -> None:
        parsed = self._write(
            tmp_path,
            [
                _call_record(1, sent=15000, received=14997),
                _call_record(2, sent=15000, received=0),
                _call_record(3, sent=15000, received=0),
            ],
        )
        assert parsed.calls_answered_with_inbound == 1
        assert parsed.calls_answered_without_inbound == 2

    def test_the_totals_cannot_see_what_this_sees(self, tmp_path: Path) -> None:
        # THE POINT OF THE WHOLE CHANGE. These two runs report identical
        # received-per-sent ratios. One is healthy and one lost half its calls
        # entirely, and only the per-call counts tell them apart.
        (tmp_path / "even").mkdir()
        (tmp_path / "split").mkdir()
        even = self._write(
            tmp_path / "even",
            [_call_record(n, sent=1000, received=500) for n in range(1, 5)],
        )
        split = self._write(
            tmp_path / "split",
            [_call_record(1, sent=1000, received=1000)] * 2
            + [_call_record(2, sent=1000, received=0)] * 2,
        )
        assert even.calls_answered_without_inbound == 0
        assert split.calls_answered_without_inbound == 2

    def test_a_failed_call_is_not_counted_as_silent(self, tmp_path: Path) -> None:
        # It never answered, so the establishment gate already owns it.
        # Counting it here would punish one failure twice.
        parsed = self._write(
            tmp_path, [_call_record(1, sent=0, received=0, success=False)]
        )
        assert parsed.calls_answered_without_inbound == 0
        assert parsed.calls_answered_with_inbound == 0

    def test_a_call_that_sent_nothing_is_not_counted(self, tmp_path: Path) -> None:
        # It never exercised the return path, so its silence says nothing
        # about the return path.
        parsed = self._write(tmp_path, [_call_record(1, sent=0, received=0)])
        assert parsed.calls_answered_without_inbound == 0

    def test_unknown_schema_leaves_the_tally_unmeasured(self, tmp_path: Path) -> None:
        # None, never zero. Zero would read as "every call got audio".
        parsed = self._write(
            tmp_path,
            [json.dumps({"schema_version": "something_else_v9", "call_number": 1})],
        )
        assert parsed.call_records_status == "present"
        assert parsed.call_records_count == 1
        assert parsed.calls_answered_without_inbound is None

    def test_absent_records_are_unmeasured_not_clean(self, tmp_path: Path) -> None:
        (tmp_path / "summary.json").write_text(
            json.dumps(
                {
                    "schema_version": "gossipper_summary_v1",
                    "total_calls": 1,
                    "success_calls": 1,
                    "failed_calls": 0,
                }
            )
        )
        parsed = art.parse_test_directory(tmp_path)
        assert parsed.call_records_status == "absent"
        assert parsed.calls_answered_without_inbound is None
