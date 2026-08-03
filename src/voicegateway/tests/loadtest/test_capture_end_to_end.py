"""A real capture, imported and reported, all the way to the exit code.

Every other test here exercises a piece. This one runs what an operator runs:
import the artifacts a generator actually wrote, export the report, and check
what came out. It is the only test that would catch a break in the seam between
two working halves, which is precisely how the import gate stayed broken while
both the parser and the CLI had passing tests.

The capture is of a run where every call failed, and that is the point twice
over. It exercises the paths a clean run never reaches, and it separates two
things that are easy to conflate:

    provenance is about WHERE THE BYTES CAME FROM.
    the verdict is about WHAT THEY SAY.

A real capture of a failed run is still a real capture. It must report
``measured`` and carry no synthetic stamp, while failing its gates and exiting
non-zero. A report that downgraded provenance because the run went badly would
be unable to describe the only runs anyone urgently needs described.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from voicegateway.cli._app import app
from voicegateway.livekit_diag import gates, run_report

runner = CliRunner()

CAPTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "loadtest" / "capture-01"
)
RUN_ID = "capture-01"


@pytest.fixture(scope="module")
def reported(tmp_path_factory):
    """Import the real capture and export its report, once.

    Into a database this fixture creates and pytest removes. VOICEGW_DB_PATH is
    cleared because a leaked env var is how a test writes to the developer's own
    voicegw.db, and this test would then be reporting on somebody's real runs.
    """
    tmp = tmp_path_factory.mktemp("capture-e2e")
    monkey = pytest.MonkeyPatch()
    monkey.delenv("VOICEGW_DB_PATH", raising=False)

    config = tmp / "voicegw.yaml"
    config.write_text(
        yaml.dump(
            {"cost_tracking": {"enabled": True, "db_path": str(tmp / "throwaway.db")}}
        )
    )
    imported = runner.invoke(
        app,
        ["loadtest", "import", str(CAPTURE), "--captured", "--config", str(config)],
    )
    out = tmp / "out"
    exported = runner.invoke(
        app,
        # --acceptance: this file asserts the exit code and the gate rows,
        # which are the acceptance view. The default report profiles and
        # deliberately carries neither.
        [
            "loadtest",
            "report",
            "--acceptance",
            RUN_ID,
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
    return {
        "imported": imported,
        "exported": exported,
        "payload": payload,
        "html": html,
        "db": tmp / "throwaway.db",
    }


# --------------------------------------------------------------------------
# The import runs at all
# --------------------------------------------------------------------------


def test_the_import_succeeds_with_no_summary(reported) -> None:
    """The shape of any run that did not finalise, which is most failed runs."""
    assert not (CAPTURE / "summary.json").exists(), "the fixture grew a summary.json"
    assert reported["imported"].exit_code == 0, reported["imported"].output


def test_it_wrote_to_the_throwaway_database(reported) -> None:
    """Guards the fixture itself: a leaked path would report on real runs."""
    assert reported["db"].is_file()
    assert "tmp" in str(reported["db"]) or "pytest" in str(reported["db"])


# --------------------------------------------------------------------------
# Provenance: about the bytes, never about the outcome
# --------------------------------------------------------------------------


def test_the_run_is_measured_not_synthetic(reported) -> None:
    payload = reported["payload"]
    assert payload["data_provenance"] == run_report.PROVENANCE_MEASURED
    assert payload["data_provenance"] != run_report.PROVENANCE_SYNTHETIC


def test_the_checksum_is_a_real_digest(reported) -> None:
    """Shape, not truthiness. "pending" in that column would read as measured."""
    checksum = reported["payload"]["run"]["artifact_sha256"]
    assert re.fullmatch(r"[0-9a-f]{64}", checksum), checksum


def test_the_html_carries_no_not_a_deliverable_stamp(reported) -> None:
    """These artifacts are real, so the report is a deliverable."""
    assert run_report.SYNTHETIC_STAMP not in reported["html"]
    assert "SYNTHETIC" not in reported["html"]


def test_a_failed_run_is_still_a_real_capture(reported) -> None:
    """The conflation this test exists to rule out.

    The verdict is FAIL and the provenance is measured, at the same time, from
    the same bytes. A report that downgraded provenance because the run went
    badly could not describe the only runs anyone urgently needs described.
    """
    payload = reported["payload"]
    assert payload["verdict"]["status"] == gates.FAIL
    assert payload["data_provenance"] == run_report.PROVENANCE_MEASURED
    # And the basis says what earned it: the artifact, not the outcome.
    assert "checksum" in payload["provenance_basis"]
    assert "artifact" in payload["provenance_basis"]


def test_provenance_does_not_read_the_verdict() -> None:
    """Structural, so it holds for runs this fixture does not cover.

    _provenance_of is handed a run row and derives from the checksum alone. A
    future writer cannot assert measured-ness without the artifact behind it,
    and cannot lose it by failing.
    """
    digest = "a" * 64
    passed = run_report._provenance_of({"artifact_sha256": digest, "verdict": "PASS"})
    failed = run_report._provenance_of({"artifact_sha256": digest, "verdict": "FAIL"})
    assert passed == failed == run_report.PROVENANCE_MEASURED
    # And no checksum is synthetic however well the run went.
    assert (
        run_report._provenance_of({"artifact_sha256": None, "verdict": "PASS"})
        == run_report.PROVENANCE_SYNTHETIC
    )


# --------------------------------------------------------------------------
# The numbers came off the stat file
# --------------------------------------------------------------------------


def test_the_counts_are_what_the_capture_recorded(reported) -> None:
    [test] = reported["payload"]["tests"]
    assert test["attempted_calls"] == 3
    assert test["succeeded_calls"] == 0
    assert test["failed_calls"] == 3


def test_peak_concurrency_is_the_max_of_active_calls(reported) -> None:
    """3, from the interval series, not from an end-of-run drain value of 0."""
    [test] = reported["payload"]["tests"]
    assert test["peak_concurrency"] == 3


def test_every_failure_is_a_timeout(reported) -> None:
    [test] = reported["payload"]["tests"]
    assert test["failures_by_cause"]["timeout"] == 3


# --------------------------------------------------------------------------
# The gates judged it, and said so
# --------------------------------------------------------------------------


def _gate(payload, name: str):
    return [g for g in payload["gates"] if g["gate"] == name]


def test_the_establishment_gate_fails(reported) -> None:
    """0.0 is below 0.995, and this is the one criterion that was measurable."""
    [gate] = _gate(reported["payload"], "call_establishment")
    assert gate["status"] == gates.FAIL
    assert gate["threshold"] == gates.MIN_ESTABLISHMENT_RATIO


def test_the_unmeasured_gates_are_unknown_not_pass(reported) -> None:
    """Nothing scraped these nodes, which is not the same as them being fine."""
    for name in ("node_cpu", "node_memory", "resource_headroom"):
        statuses = {g["status"] for g in _gate(reported["payload"], name)}
        assert statuses == {gates.UNKNOWN}, (name, statuses)


def test_cpu_and_memory_are_null_with_a_reason(reported) -> None:
    """NULL, never 0.0, and the report says why rather than going quiet."""
    [test] = reported["payload"]["tests"]
    assert test["peak_cpu_utilisation"] is None
    assert test["peak_memory_utilisation"] is None
    for name in ("node_cpu", "node_memory"):
        [gate] = _gate(reported["payload"], name)
        assert gate["detail"], name
        assert "no node was sampled" in gate["detail"], gate["detail"]


def test_the_reason_reaches_the_html(reported) -> None:
    """A reason nobody reading the deliverable can see is not a reason."""
    assert "not measured" in reported["html"].lower()


# --------------------------------------------------------------------------
# The exit code
# --------------------------------------------------------------------------


def test_the_cli_exits_non_zero(reported) -> None:
    """A pipeline must not go green on a run that failed its acceptance gate."""
    assert reported["exported"].exit_code != 0


def test_the_evidence_was_written_before_the_exit(reported) -> None:
    """A failing run is exactly the run whose report somebody needs."""
    assert reported["payload"]["gates"]
    assert reported["payload"]["gates_recorded"] is True
    assert reported["html"]
