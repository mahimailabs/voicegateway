"""Planning an import: what gets written, and what is refused.

Two properties carry this module.

**Provenance under-claims.** A run is synthetic unless the operator declares the
artifacts captured. A forgotten flag produces an under-claim, never a report
that presents fixture numbers as measured.

**Nothing is invented for a column the artifacts cannot fill.** Per-call
observations are the sharp case: the endpoint takes one call at a time and
correlates on room_sid or attempt_id, and an aggregate summary carries neither.
Deriving 15,000 of them from a 15,000-call total would create calls nobody
observed that are then indistinguishable from real ones.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voicegateway.loadtest import importer
from voicegateway.loadtest.artifacts import MissingArtifact
from voicegateway.tests.loadtest.test_artifacts import STAT_HEADER, STAT_ROWS, SUMMARY

NOW = 1_785_600_000_000


def _test_dir(directory: Path, *, summary: dict | None = SUMMARY) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    if summary is not None:
        (directory / "summary.json").write_text(json.dumps(summary))
    (directory / "stat.csv").write_text(
        STAT_HEADER + "\n" + "\n".join(STAT_ROWS) + "\n"
    )
    return directory


@pytest.fixture
def one_test(tmp_path: Path) -> Path:
    return _test_dir(tmp_path / "ramp-500")


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


def test_an_import_is_synthetic_unless_captured_is_declared(one_test: Path) -> None:
    """The default has to be the under-claim.

    has_artifact is derived from artifact_sha256, so withholding it is what
    makes every downstream report stamp itself synthetic.
    """
    plan = importer.build_plan(one_test, now_ms=NOW)
    assert plan.run.artifact_sha256 is None
    assert plan.is_synthetic is True


def test_declaring_captured_promotes_the_checksum(one_test: Path) -> None:
    plan = importer.build_plan(one_test, captured=True, now_ms=NOW)
    assert plan.run.artifact_sha256 is not None
    assert len(plan.run.artifact_sha256) == 64
    assert plan.is_synthetic is False


def test_the_checksum_is_recorded_even_on_a_synthetic_import(one_test: Path) -> None:
    """Traceability is not what provenance gates. Only the claim is."""
    plan = importer.build_plan(one_test, now_ms=NOW)
    captured = importer.build_plan(one_test, captured=True, now_ms=NOW)
    assert captured.run.artifact_sha256 in plan.run.notes
    assert "synthetic" in plan.run.notes.lower()


def test_the_checksum_changes_when_the_bytes_change(tmp_path: Path) -> None:
    a = importer.build_plan(_test_dir(tmp_path / "a"), captured=True, now_ms=NOW)
    altered = dict(SUMMARY)
    altered["total_calls"] = 14999
    b = importer.build_plan(
        _test_dir(tmp_path / "b", summary=altered), captured=True, now_ms=NOW
    )
    assert a.run.artifact_sha256 != b.run.artifact_sha256


def test_the_checksum_is_stable_across_repeated_reads(one_test: Path) -> None:
    """A re-import of untouched artifacts must not look like different bytes."""
    first = importer.build_plan(one_test, captured=True, now_ms=NOW)
    second = importer.build_plan(one_test, captured=True, now_ms=NOW + 5000)
    assert first.run.artifact_sha256 == second.run.artifact_sha256


# --------------------------------------------------------------------------
# Per-call observations
# --------------------------------------------------------------------------


def test_no_per_call_observations_are_invented_from_aggregates(
    one_test: Path,
) -> None:
    """The centrepiece. 15,000 calls in the totals, zero observable calls.

    An aggregate carries no room_sid and no attempt_id, so there is nothing to
    correlate on. Synthesising one observation per counted call would fabricate
    15,000 calls that no process saw.
    """
    plan = importer.build_plan(one_test, now_ms=NOW)
    assert plan.tests[0].attempted_calls == 15000
    assert importer.observations_for(plan.parsed[0]) == []


def test_the_absence_of_per_call_records_is_stated_not_hidden(
    one_test: Path,
) -> None:
    plan = importer.build_plan(one_test, now_ms=NOW)
    assert any("per-call" in line for line in plan.limitations)


def test_present_but_uninterpretable_records_are_reported_as_such(
    one_test: Path,
) -> None:
    (one_test / "calls.jsonl").write_text('{"a": 1}\n{"b": 2}\n')
    plan = importer.build_plan(one_test, now_ms=NOW)
    assert any("2 call records" in line for line in plan.limitations)
    # Counted, still not interpreted, still no observations.
    assert importer.observations_for(plan.parsed[0]) == []


# --------------------------------------------------------------------------
# Discovery and sequencing
# --------------------------------------------------------------------------


def test_a_directory_of_artifacts_is_one_test(one_test: Path) -> None:
    assert importer.discover_tests(one_test) == [one_test]


def test_subdirectories_become_tests_in_name_order(tmp_path: Path) -> None:
    """A ramp's steps only mean anything in order."""
    root = tmp_path / "ramp"
    for name in ("step-300", "step-100", "step-500"):
        _test_dir(root / name)
    plan = importer.build_plan(root, now_ms=NOW)
    assert [t.name for t in plan.tests] == ["step-100", "step-300", "step-500"]
    assert [t.sequence for t in plan.tests] == [0, 1, 2]


def test_an_empty_directory_is_refused_with_a_named_error(tmp_path: Path) -> None:
    empty = tmp_path / "nothing"
    empty.mkdir()
    with pytest.raises(MissingArtifact):
        importer.build_plan(empty, now_ms=NOW)


def test_the_run_id_defaults_to_the_directory_name(one_test: Path) -> None:
    """So a re-import updates the same run instead of making a second one."""
    assert importer.build_plan(one_test, now_ms=NOW).run.id == "ramp-500"
    assert importer.build_plan(one_test, run_id="x", now_ms=NOW).run.id == "x"


def test_the_run_window_spans_every_test(tmp_path: Path) -> None:
    root = tmp_path / "ramp"
    early = dict(SUMMARY)
    early["started_at"] = "2026-07-31T10:00:00Z"
    early["finished_at"] = "2026-07-31T11:00:00Z"
    _test_dir(root / "a", summary=early)
    _test_dir(root / "b")
    plan = importer.build_plan(root, now_ms=NOW)
    # The earliest start across the tests (10:00Z) through the latest end
    # (19:00Z), not either test's own window.
    assert plan.run.started_at_ms == 1785492000000  # 2026-07-31T10:00:00Z
    assert plan.run.ended_at_ms == 1785524400000  # 2026-07-31T19:00:00Z
    assert plan.run.ended_at_ms - plan.run.started_at_ms == 9 * 3_600_000


# --------------------------------------------------------------------------
# The mapping onto load_run_tests
# --------------------------------------------------------------------------


def test_each_measured_column_lands_on_its_own_column(one_test: Path) -> None:
    [test] = importer.build_plan(one_test, now_ms=NOW).tests
    assert test.peak_concurrency == 492
    assert test.attempted_calls == 15000
    assert test.succeeded_calls == 14985
    assert test.failed_calls == 15
    assert test.failed_timeout == 3
    assert test.failed_unexpected_sip == 9
    assert test.rtp_packets_sent == 88410000


def test_a_cause_the_artifacts_omitted_stays_none(tmp_path: Path) -> None:
    """None, not 0. A 0 would claim the generator saw no timeouts."""
    partial = dict(SUMMARY)
    partial["failure_classes"] = {"timeout": 3}
    [test] = importer.build_plan(
        _test_dir(tmp_path / "p", summary=partial), now_ms=NOW
    ).tests
    assert test.failed_timeout == 3
    assert test.failed_cancelled is None
    assert test.failed_parse_error is None


def test_columns_this_node_cannot_fill_are_left_none(one_test: Path) -> None:
    """target_concurrency lives in the scenario file; the node peaks are DATA4's.

    Writing a 0 or copying peak_concurrency across would make a later
    correlation pass look like it had already run.
    """
    [test] = importer.build_plan(one_test, now_ms=NOW).tests
    assert test.target_concurrency is None
    assert test.peak_cpu_utilisation is None
    assert test.peak_memory_utilisation is None
    assert test.node_samples_in_window is None


def test_one_tool_is_asserted_only_when_the_tests_agree(tmp_path: Path) -> None:
    """Two generators in one run is a fact, not something to flatten."""
    root = tmp_path / "mixed"
    _test_dir(root / "a")
    other = dict(SUMMARY)
    other["tool_version"] = "somethingelse 9.9"
    _test_dir(root / "b", summary=other)
    plan = importer.build_plan(root, now_ms=NOW)
    assert plan.run.tool is None
    assert plan.run.tool_version is None

    agreed = importer.build_plan(_test_dir(tmp_path / "solo"), now_ms=NOW)
    assert agreed.run.tool == "gossipper"


def test_the_plan_records_where_the_artifacts_came_from(one_test: Path) -> None:
    plan = importer.build_plan(one_test, now_ms=NOW)
    assert plan.run.artifact_source == str(one_test.resolve())


def test_a_missing_csv_is_reported_as_unmeasured_concurrency(tmp_path: Path) -> None:
    directory = tmp_path / "summary-only"
    directory.mkdir()
    (directory / "summary.json").write_text(json.dumps(SUMMARY))
    plan = importer.build_plan(directory, now_ms=NOW)
    assert plan.tests[0].peak_concurrency is None
    assert any("peak concurrency is not measured" in x for x in plan.limitations)
