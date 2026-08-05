"""The parser against a real generator run, which it could not read at all.

Every fixture before this one was hand-built from the generator's published
documentation. The documentation was right about the header and silent about two
things that decided whether an import worked:

**The stat file is not named ``.csv``.** The generator writes
``gossipper_<pid>_stats.log``: a CSV by content, a ``.log`` by name. Discovery
globbed ``*.csv``, so a real run's stat file was never a candidate and the
header check that would have recognised it never ran. The import failed with
"neither surface is present" while sitting in a directory containing a valid
43-column CSV with 65 rows.

**``summary.json`` is often absent.** This run was killed before it finalised,
which is the normal outcome for any run that did not end cleanly, and exactly
the run somebody needs a report for.

The capture also fails every call, which is what makes it worth keeping. A clean
run never exercises the paths where a real zero has to be told apart from a gap.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voicegateway.loadtest import artifacts

CAPTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "loadtest" / "capture-01"
)
STAT_FILE = "gossipper_2958087_stats.log"


@pytest.fixture(scope="module")
def parsed():
    return artifacts.parse_test_directory(CAPTURE)


# --------------------------------------------------------------------------
# Discovery ignores the extension
# --------------------------------------------------------------------------


def test_the_capture_is_shaped_the_way_the_bug_needed() -> None:
    """Guards the premise. Renaming the fixture would void every test here."""
    names = {p.name for p in CAPTURE.iterdir()}
    assert STAT_FILE in names
    assert not any(n.endswith(".csv") for n in names), (
        "the fixture has acquired a .csv file, so it no longer reproduces the bug"
    )
    assert "summary.json" not in names, "the run never finalised; that is the point"


def test_the_stat_file_is_found_despite_its_extension() -> None:
    found = artifacts._find_stat_file(CAPTURE)
    assert found is not None, "a real run's stat file was not found"
    assert found.name == STAT_FILE


def test_it_is_selected_by_header_not_by_name(tmp_path: Path) -> None:
    """Non-vacuous: the fix is not "also accept .log".

    A file with an unremarkable name and the right header is the stat file; a
    file named to look like one without the header is not.
    """
    header = (CAPTURE / STAT_FILE).read_text().splitlines()[0]
    (tmp_path / "anything.txt").write_text(header + "\n")
    assert artifacts._find_stat_file(tmp_path).name == "anything.txt"

    other = tmp_path / "decoy"
    other.mkdir()
    (other / "gossipper_1_stats.log").write_text("not,a,stat,header\n1,2,3,4\n")
    assert artifacts._find_stat_file(other) is None


def test_a_binary_file_does_not_break_discovery(tmp_path: Path) -> None:
    """Discovery reads every file, so it meets things that are not text."""
    (tmp_path / "capture.pcap").write_bytes(bytes(range(256)) * 8)
    (tmp_path / STAT_FILE).write_text((CAPTURE / STAT_FILE).read_text())
    assert artifacts._find_stat_file(tmp_path).name == STAT_FILE


def test_the_other_surfaces_are_not_mistaken_for_the_stat_file(
    tmp_path: Path,
) -> None:
    """calls.jsonl and summary.json are skipped by NAME, not by extension."""
    (tmp_path / "calls.jsonl").write_text((CAPTURE / "calls.jsonl").read_text())
    (tmp_path / "summary.json").write_text("{}")
    assert artifacts._find_stat_file(tmp_path) is None


# --------------------------------------------------------------------------
# The import succeeds from the stat file alone
# --------------------------------------------------------------------------


def test_a_run_with_no_summary_still_imports(parsed) -> None:
    """The failure this whole file exists for, end to end."""
    assert parsed.attempted_calls == 3
    assert parsed.succeeded_calls == 0
    assert parsed.failed_calls == 3


def test_the_interval_series_is_read_whole(parsed) -> None:
    assert len(parsed.samples) == 64  # 65 lines, one of them the header


def test_peak_concurrency_comes_from_the_intervals(parsed) -> None:
    """Not from an end-of-run drain value, which is 0 here and would read clean."""
    assert parsed.peak_concurrency == 3
    assert parsed.samples[-1].active_calls == 0


def test_the_failure_breakdown_survives(parsed) -> None:
    """The one thing this run has to say: every call timed out."""
    assert parsed.failures_by_cause["timeout"] == 3
    assert sum(parsed.failures_by_cause.values()) == 3


def test_the_window_is_derived_from_the_timestamps(parsed) -> None:
    assert parsed.started_at_ms == 1785616098598
    assert parsed.ended_at_ms == 1785616160603
    assert parsed.duration_ms == 63005


# --------------------------------------------------------------------------
# A zero that was measured is not a gap
# --------------------------------------------------------------------------


def test_a_measured_zero_is_not_none(parsed) -> None:
    """No media flowed, and that is a reading rather than a missing one.

    Rendering it as absent would hide the finding; rendering an absent value as
    0 would invent one. This run is the case that tells the two apart.
    """
    assert parsed.rtp_packets_sent == 0
    assert parsed.rtp_packets_received == 0
    assert parsed.succeeded_calls == 0
    for value in (
        parsed.rtp_packets_sent,
        parsed.rtp_packets_received,
        parsed.succeeded_calls,
    ):
        assert value is not None


def test_what_the_stat_file_cannot_supply_stays_none(parsed) -> None:
    """The counterpart. Only summary.json carries these, and there is none."""
    assert parsed.answer_latency is None
    assert parsed.tool is None
    assert parsed.artifact_schema_version is None


# --------------------------------------------------------------------------
# The call records, whose schema this capture is the only source for
# --------------------------------------------------------------------------


def test_the_call_records_are_counted(parsed) -> None:
    assert parsed.call_records_status == "present"
    assert parsed.call_records_count == 3


def test_the_recorded_schema_matches_the_capture() -> None:
    """The schema is in the module docstring because it exists nowhere else.

    Checked against the real file so the note cannot rot into a description of
    a shape the generator stopped emitting.
    """
    records = [
        json.loads(line)
        for line in (CAPTURE / "calls.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert records
    doc = artifacts._read_call_records.__doc__ or ""
    for record in records:
        assert record["schema_version"] == "gossipper_call_record_v1"
        for key in ("call_id", "call_number", "success", "duration_ms", "error"):
            assert key in record, key
            assert key in doc, f"{key} is in the capture but not in the note"
        for key in record["media"]:
            assert key in doc, f"media.{key} is in the capture but not in the note"


def test_the_media_keys_are_pascal_case() -> None:
    """Pinned because it is the detail a mapping written from memory gets wrong.

    Every other surface here is snake_case, so this is the one place a
    reasonable guess produces a mapping that silently reads nothing.
    """
    [first, *_] = [
        json.loads(line)
        for line in (CAPTURE / "calls.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert "RTPPacketsSent" in first["media"]
    assert "rtp_packets_sent" not in first["media"]


def test_a_malformed_record_does_not_fail_the_import(tmp_path: Path) -> None:
    """This surface is enrichment. It must never take an import down."""
    (tmp_path / STAT_FILE).write_text((CAPTURE / STAT_FILE).read_text())
    (tmp_path / "calls.jsonl").write_text('{"ok":1}\nnot json at all\n')
    result = artifacts.parse_test_directory(tmp_path)
    assert result.attempted_calls == 3
    assert result.call_records_status in {"present", "partial", "unreadable"}
