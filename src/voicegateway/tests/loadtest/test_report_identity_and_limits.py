"""What the report says about itself, and about this run in particular.

Three gaps, all of which cost a reader something specific.

**When the run happened.** The window was in the payload and nowhere in the
document, which showed only when the EXPORT was produced. That date can be
months later and says nothing about the test, so the report could not be matched
to a change window, an incident, or the hours it measured.

**What THIS run could not answer.** The import computed those and printed them
to whoever ran it, then dropped them. They are the answer to "why is this column
empty", and without them a reader cannot tell a quantity nothing can measure
from one that simply was not measured this time.

**Which of two causes applied.** The answer-latency note named a scenario
setting, and the scenario used for the real capture HAS that setting. Every call
timed out before 200 OK, so no round-trip delay could be sampled however the
scenario was written. Naming the wrong cause sends somebody to edit a file that
is already correct.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from voicegateway.cli._app import app
from voicegateway.livekit_diag import run_report
from voicegateway.loadtest.artifacts import ParsedTest
from voicegateway.loadtest.importer import (
    LIMITATIONS_HEADING,
    _limitations_for,
    limitations_from_notes,
)

runner = CliRunner()

CAPTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "loadtest" / "capture-01"
)


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("identity")
    monkey = pytest.MonkeyPatch()
    monkey.delenv("VOICEGW_DB_PATH", raising=False)
    config = tmp / "voicegw.yaml"
    config.write_text(
        yaml.dump(
            {"cost_tracking": {"enabled": True, "db_path": str(tmp / "throwaway.db")}}
        )
    )
    assert (
        runner.invoke(
            app,
            ["loadtest", "import", str(CAPTURE), "--captured", "--config", str(config)],
        ).exit_code
        == 0
    )
    out = tmp / "out"
    runner.invoke(
        app,
        [
            "loadtest",
            "report",
            "capture-01",
            "--config",
            str(config),
            "--out",
            str(out),
        ],
    )
    payload = json.loads(
        next(p for p in out.iterdir() if p.suffix == ".json").read_text()
    )
    html = next(p for p in out.iterdir() if p.suffix == ".html").read_text()
    monkey.undo()
    return {"payload": payload, "html": html}


# --------------------------------------------------------------------------
# 1. When the run happened, and which artifacts it describes
# --------------------------------------------------------------------------


def test_the_run_window_is_in_the_document(exported) -> None:
    """Derived from the payload, so the two exports cannot disagree."""
    run = exported["payload"]["run"]
    started = run_report._utc(run["started_at_ms"])
    ended = run_report._utc(run["ended_at_ms"])
    assert started and ended
    assert started in exported["html"]
    assert ended in exported["html"]


def test_the_window_is_labelled_utc(exported) -> None:
    """A bare timestamp is unresolvable by whoever reads this months later."""
    assert "Run window (UTC)" in exported["html"]
    assert run_report._utc(exported["payload"]["run"]["started_at_ms"]).endswith("Z")


def test_the_window_is_not_the_export_time(exported) -> None:
    """The footer date is when the file was made, not when the test ran.

    Showing only that was the defect: it looks like a date and answers a
    different question.
    """
    generated = exported["payload"]["generated_at"]
    started = run_report._utc(exported["payload"]["run"]["started_at_ms"])
    assert not generated.startswith(started[:10] + "T" + started[11:19])


def test_the_artifact_checksum_identifies_the_document(exported) -> None:
    """Two reports of two runs must be tellable apart without opening the JSON."""
    checksum = exported["payload"]["run"]["artifact_sha256"]
    assert checksum[:16] in exported["html"]


def test_an_unrecorded_window_says_so_rather_than_vanishing() -> None:
    """A header that silently drops it reads as a report that never had one."""
    html = run_report.render_load_html(
        run_report.build_load_payload(
            run={"id": "r", "artifact_sha256": None}, tests=[]
        )
    )
    assert "Run window (UTC)" in html
    assert "not recorded" in html
    assert "no artifact checksum" in html


# --------------------------------------------------------------------------
# 2. This run's own gaps reach the reader
# --------------------------------------------------------------------------


def test_the_run_limitations_reach_the_payload(exported) -> None:
    assert exported["payload"]["run_limitations"]


def test_they_are_rendered_apart_from_the_structural_limits(exported) -> None:
    """One says never, the other says not this run. Merging them misleads twice.

    A permanent limit shown as this run's bad luck invites a pointless re-run; a
    fixable gap shown as permanent stops somebody fixing it.
    """
    html = exported["html"]
    assert "what THIS run did not measure" in html
    assert "not limits of the system" in html
    structural = set(exported["payload"]["not_measured"])
    run_specific = set(exported["payload"]["run_limitations"])
    assert not structural & run_specific


def test_each_run_limitation_is_in_the_html(exported) -> None:
    for item in exported["payload"]["run_limitations"]:
        assert run_report._esc(item) in exported["html"], item


def test_a_run_with_no_gaps_shows_no_empty_section() -> None:
    """An empty heading reads as a section somebody forgot to fill in."""
    html = run_report.render_load_html(
        run_report.build_load_payload(
            run={"id": "r", "artifact_sha256": "a" * 64}, tests=[], limitations=[]
        )
    )
    assert "what THIS run did not measure" not in html
    # The structural list is unaffected either way.
    assert "RTP-port headroom" in html


def test_the_notes_round_trip_survives(exported) -> None:
    """The format is load-bearing, since the report is exported by a LATER
    command reading the row rather than the artifacts."""
    notes = f"artifact sha256: {'a' * 64}\n\n{LIMITATIONS_HEADING}\n- one\n- two"
    assert limitations_from_notes(notes) == ["one", "two"]


def test_a_row_written_before_this_reads_as_no_gaps_noted() -> None:
    """Absence of a record is not a record of absence, and not an error."""
    assert limitations_from_notes("artifact sha256: abc") == []
    assert limitations_from_notes(None) == []


def test_a_later_section_is_not_swallowed() -> None:
    notes = f"{LIMITATIONS_HEADING}\n- one\n\nSomething else entirely\n- not mine"
    assert limitations_from_notes(notes) == ["one"]


# --------------------------------------------------------------------------
# 3. The answer-latency note names the cause that actually applied
# --------------------------------------------------------------------------


def _parsed(**kw) -> ParsedTest:
    base = {
        "name": "ramp-500",
        "peak_concurrency": 3,
        "attempted_calls": 3,
        "succeeded_calls": 0,
        "failed_calls": 3,
        "failures_by_cause": {"timeout": 3},
        "answer_latency": None,
        "call_records_status": "absent",
        "call_records_count": None,
    }
    base.update(kw)
    return ParsedTest(
        **{k: v for k, v in base.items() if k in ParsedTest.__annotations__}
    )


def test_nothing_answered_blames_the_run_not_the_scenario(exported) -> None:
    """The real capture. Every call timed out before 200 OK.

    The scenario it used carries start_rtd="answer"; naming that setting sent a
    reader to fix a file that was already correct.
    """
    [latency] = [
        item
        for item in exported["payload"]["run_limitations"]
        if "answer latency" in item
    ]
    assert "reached 200 OK" in latency
    assert "says nothing about the generator's scenario" in latency
    assert "start_rtd" not in latency


def test_calls_answered_but_no_latency_points_at_the_scenario() -> None:
    """The other cause, which is the one the old wording always claimed."""
    [latency] = [
        item
        for item in _limitations_for(_parsed(succeeded_calls=2900))
        if "answer latency" in item
    ]
    assert "start_rtd" in latency
    assert "2900 calls established" in latency


def test_unknown_totals_attribute_nothing() -> None:
    """With no counts, neither cause can be asserted, so neither is."""
    [latency] = [
        item
        for item in _limitations_for(
            _parsed(attempted_calls=None, succeeded_calls=None)
        )
        if "answer latency" in item
    ]
    assert "cannot be attributed" in latency
    assert "start_rtd" not in latency


def test_the_record_note_no_longer_claims_the_schema_is_undocumented(
    exported,
) -> None:
    """It was captured and written down; the gap is that nothing maps it."""
    [records] = [
        item
        for item in exported["payload"]["run_limitations"]
        if "call records" in item
    ]
    assert "no documented record schema" not in records
    assert "schema is known and recorded" in records
