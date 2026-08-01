"""The gate in front of the parser, which turned away every real capture.

Stat-file discovery was fixed to select by header rather than by extension, and
the gate standing in front of it was not. ``_has_artifacts`` still asked for a
``*.csv``, so a real run, whose stat file is named ``gossipper_<pid>_stats.log``,
was rejected before the parser was ever called: ``discover_tests`` returned [],
and the caller was told there were no artifacts while standing in a directory
holding a valid 43-column CSV with 65 rows.

The fix is not "also accept .log". It is that the gate asks the parser's own
finder. Two places deciding "is this a stat file" by two criteria is what
produced this, and a second rule written to agree with the first only agrees
until one of them changes.

The same mistake had a second instance nothing had noticed. ``_artifact_files``,
which decides what goes into the run checksum, also filtered by extension, so
the checksum covered ``calls.jsonl`` and silently omitted the file every number
in the row came from. That one does not fail loudly: it produces a checksum that
claims to answer "which artifacts was this row built from" and does not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voicegateway.loadtest import importer
from voicegateway.loadtest.artifacts import MissingArtifact

CAPTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "loadtest" / "capture-01"
)
STAT_FILE = "gossipper_2958087_stats.log"
# build_plan stamps the row; a fixed value keeps these deterministic.
NOW_MS = 1_785_616_200_000


def _stat_header() -> str:
    return (CAPTURE / STAT_FILE).read_text().splitlines()[0]


def _real_shaped(tmp_path: Path) -> Path:
    """A directory in the exact shape of a real capture.

    A stat file named the way the generator names it, a call-record file, and
    NO summary.json, because a run that did not finalise never writes one.
    """
    run = tmp_path / "ramp-500"
    run.mkdir(parents=True)
    (run / STAT_FILE).write_text((CAPTURE / STAT_FILE).read_text())
    (run / "calls.jsonl").write_text((CAPTURE / "calls.jsonl").read_text())
    return run


# --------------------------------------------------------------------------
# The blocker
# --------------------------------------------------------------------------


def test_the_real_capture_is_discovered() -> None:
    """The failure this file exists for. It used to return []."""
    assert importer.discover_tests(CAPTURE) == [CAPTURE]


def test_a_directory_in_the_real_shape_imports(tmp_path: Path) -> None:
    """Only a stat .log and calls.jsonl, no summary.json. The common case.

    Any run that was killed before it finalised looks exactly like this, which
    is the run somebody most needs a report for.
    """
    run = _real_shaped(tmp_path)
    assert importer.discover_tests(run) == [run]
    plan = importer.build_plan(run, captured=True, now_ms=NOW_MS)
    assert plan.tests
    assert plan.tests[0].attempted_calls == 3


def test_a_run_directory_of_subdirectories_still_works(tmp_path: Path) -> None:
    """The multi-test layout: each subdirectory is one test, ordered by name."""
    root = tmp_path / "run"
    root.mkdir()
    for name in ("ramp-500", "ramp-100", "ramp-300"):
        step = root / name
        step.mkdir()
        (step / STAT_FILE).write_text((CAPTURE / STAT_FILE).read_text())
    assert [p.name for p in importer.discover_tests(root)] == [
        "ramp-100",
        "ramp-300",
        "ramp-500",
    ]


# --------------------------------------------------------------------------
# The trap: an empty directory is still rejected
# --------------------------------------------------------------------------


def test_a_directory_with_neither_surface_is_still_rejected(tmp_path: Path) -> None:
    """The false negative must not be fixed by accepting everything."""
    empty = tmp_path / "nothing"
    empty.mkdir()
    (empty / "README.md").write_text("notes about the run\n")
    (empty / "screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(64))
    assert importer.discover_tests(empty) == []
    with pytest.raises(MissingArtifact) as excinfo:
        importer.build_plan(empty, captured=True, now_ms=NOW_MS)
    assert "no artifacts here" in str(excinfo.value)


def test_a_file_named_like_a_stat_file_without_the_header_is_rejected(
    tmp_path: Path,
) -> None:
    """Non-vacuous: the name is not what is being accepted. The header is."""
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    (decoy / "gossipper_999_stats.log").write_text("some,other,columns\n1,2,3\n")
    assert importer.discover_tests(decoy) == []


def test_an_unnamed_file_with_the_right_header_is_accepted(tmp_path: Path) -> None:
    """The other half of the same property, so neither can pass by name alone."""
    odd = tmp_path / "odd"
    odd.mkdir()
    (odd / "whatever").write_text(_stat_header() + "\n")
    assert importer.discover_tests(odd) == [odd]


def test_a_binary_file_does_not_break_the_gate(tmp_path: Path) -> None:
    """The gate reads first lines now, so it meets things that are not text."""
    run = _real_shaped(tmp_path)
    (run / "capture.pcap").write_bytes(bytes(range(256)) * 64)
    assert importer.discover_tests(run) == [run]


def test_the_rejection_does_not_blame_the_extension(tmp_path: Path) -> None:
    """The old message said "a stat CSV", which sends the reader to rename it.

    Renaming a real capture's stat file to .csv is exactly the wrong fix and it
    would have worked, which is how a message can make a bug permanent.
    """
    empty = tmp_path / "nothing"
    empty.mkdir()
    with pytest.raises(MissingArtifact) as excinfo:
        importer.build_plan(empty, captured=True, now_ms=NOW_MS)
    assert "stat CSV" not in str(excinfo.value)
    assert "header" in str(excinfo.value)


# --------------------------------------------------------------------------
# The second instance: the checksum omitted the stat file
# --------------------------------------------------------------------------


def test_the_stat_file_is_checksummed() -> None:
    """It carries every number in the row, and it was not being hashed."""
    names = [p.name for p in importer._artifact_files(CAPTURE)]
    assert STAT_FILE in names
    assert "calls.jsonl" in names


def test_two_runs_differing_only_in_measurements_do_not_hash_alike(
    tmp_path: Path,
) -> None:
    """What the omission actually cost, stated as the property it broke.

    The checksum answers "which artifacts was this row built from". With the
    stat file left out, two runs sharing a calls.jsonl and carrying completely
    different measurements produced the same answer.
    """
    a = _real_shaped(tmp_path / "a")
    b = _real_shaped(tmp_path / "b")
    rows = (b / STAT_FILE).read_text().splitlines()
    # Change one measured value in the last interval, nothing else.
    rows[-1] = rows[-1].replace(",3,0,", ",99,0,", 1)
    (b / STAT_FILE).write_text("\n".join(rows) + "\n")

    assert importer.checksum_artifacts([a]) != importer.checksum_artifacts([b])


def test_a_csv_stat_file_is_counted_once(tmp_path: Path) -> None:
    """A stat file that IS a .csv matches both rules and must not be hashed twice."""
    run = tmp_path / "csv-run"
    run.mkdir()
    (run / "stats.csv").write_text((CAPTURE / STAT_FILE).read_text())
    names = [p.name for p in importer._artifact_files(run)]
    assert names == ["stats.csv"]


def test_the_checksum_still_covers_the_named_surfaces(tmp_path: Path) -> None:
    """Non-vacuous: adding the stat file must not have dropped the others."""
    run = _real_shaped(tmp_path)
    (run / "summary.json").write_text('{"total_calls": 3}')
    names = [p.name for p in importer._artifact_files(run)]
    assert set(names) == {STAT_FILE, "calls.jsonl", "summary.json"}


# --------------------------------------------------------------------------
# One rule, not two that agree by inspection
# --------------------------------------------------------------------------


def test_the_gate_and_the_parser_use_the_same_finder() -> None:
    """Checked by identity, because agreeing today is not the property.

    The bug was two rules that agreed when they were written. What is needed is
    that there is only one, so they cannot drift apart again.
    """
    from voicegateway.loadtest import artifacts

    assert importer._find_stat_file is artifacts._find_stat_file


def test_the_gate_agrees_with_the_parser_on_the_real_capture() -> None:
    """The behavioural counterpart: same directory, same answer."""
    from voicegateway.loadtest import artifacts

    assert artifacts._find_stat_file(CAPTURE) is not None
    assert importer._has_artifacts(CAPTURE) is True
