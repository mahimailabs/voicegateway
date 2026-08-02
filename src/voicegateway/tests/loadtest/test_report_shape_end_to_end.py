"""The reshaped report, regenerated from the real capture and read.

Four changes land here at once, and this is the only place that checks them
against the artifact a client's run actually produces rather than against a
hand-built payload. A shape that is right in a unit test and wrong in the
exported file has not been fixed.

Every remaining UNKNOWN must name a measurement that WAS attempted, or a
resource declared out of scope. That is the whole distinction the reshaping
turns on: an UNKNOWN for something nobody tried is noise, and there were
thirty-three of them above the table the client contracted for.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from voicegateway.cli._app import app
from voicegateway.livekit_diag import gates, run_report
from voicegateway.loadtest import judge

runner = CliRunner()

CAPTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "loadtest" / "capture-01"
)

# Imported from the tests that own them, so self-containment cannot drift here
# while this file looks like it enforces it.
from voicegateway.tests.cli.test_livekit_report_cli import (  # noqa: E402
    _EXTERNAL_MARKERS as CLI_MARKERS,
)
from voicegateway.tests.server.test_diagnostics_report import (  # noqa: E402
    _EXTERNAL_MARKERS as SERVER_MARKERS,
)


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    """Import the real capture and export both artifacts, throwaway db."""
    tmp = tmp_path_factory.mktemp("shape")
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
    result = runner.invoke(
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
    return {"result": result, "payload": payload, "html": html}


# --------------------------------------------------------------------------
# Materially fewer rows, and every survivor earns its place
# --------------------------------------------------------------------------


def test_the_gate_table_is_materially_smaller(exported) -> None:
    """A real three-step run rendered 48 rows, 33 of them UNKNOWN.

    Bounded by the SHAPE rather than by a number measured off one artifact. This
    fixture has no node samples, so it produces far fewer rows than a scraped
    run, and an absolute ceiling tuned to it would fail the moment somebody
    imported a run that actually had metrics. What must hold either way is that
    rows scale with tests and measured nodes, not with the cross product of
    tests, sources and resources.
    """
    payload = exported["payload"]
    tests = max(1, len(payload["tests"]))
    # One establishment gate per test, node CPU and memory per measured node,
    # file descriptors per measured node, and two fleet-wide exclusions.
    assert len(payload["gates"]) <= tests * 6 + 2


def test_every_unknown_names_an_attempt_or_a_scope_exclusion(exported) -> None:
    """The distinction the whole reshaping turns on.

    An UNKNOWN for a measurement nobody attempted is noise. One for a
    measurement that was attempted and produced nothing is the signal the status
    exists to raise, and one for a resource nothing can measure is a declared
    exclusion. Every remaining row must be one of the latter two.
    """
    excluded = set(judge.excluded_headroom_resources())
    for gate in exported["payload"]["gates"]:
        if gate["status"] != gates.UNKNOWN:
            continue
        subject = gate["subject"] or ""
        resource = subject.rsplit("/", 1)[-1]
        attempted = "no node was sampled in the window" in gate["detail"]
        assert attempted or resource in excluded, gate


def test_no_gate_grades_a_source_for_a_metric_it_does_not_report(exported) -> None:
    """livekit-sip and livekit-server publish no node-wide series.

    Twelve of the original rows graded them for node CPU and memory, which says
    a measurement failed where none was possible.
    """
    for gate in exported["payload"]["gates"]:
        if gate["gate"] not in ("node_cpu", "node_memory"):
            continue
        subject = gate["subject"] or ""
        assert "livekit-sip" not in subject, gate
        assert "livekit-server" not in subject, gate


def test_the_two_unmeasurable_resources_appear_once_each(exported) -> None:
    rows = [
        g["subject"]
        for g in exported["payload"]["gates"]
        if (g["subject"] or "").endswith(("/rtp_ports", "/network"))
    ]
    assert sorted(rows) == ["fleet/network", "fleet/rtp_ports"]


# --------------------------------------------------------------------------
# The answer above the working
# --------------------------------------------------------------------------


def test_the_per_test_table_precedes_the_gate_detail(exported) -> None:
    html = exported["html"]
    assert html.index("Per test") < html.index("<h2>Gates</h2>")


def test_the_exclusions_sit_above_the_results(exported) -> None:
    """They qualify the verdict, so they come with it rather than after."""
    html = exported["html"]
    assert html.index('class="verdict') < html.index("Not in scope for this report")
    assert html.index("Not in scope for this report") < html.index("Per test")


# --------------------------------------------------------------------------
# Both exclusions, with reasons, and not repeated into every row
# --------------------------------------------------------------------------


def test_both_exclusions_are_present_with_reasons(exported) -> None:
    exclusions = exported["payload"]["scope_exclusions"]
    assert sorted(exclusions) == ["network", "rtp_ports"]
    for reason in exclusions.values():
        assert run_report._esc(reason) in exported["html"]


def test_the_criterion_is_not_claimed_as_covered(exported) -> None:
    assert "not fully covered" in exported["html"]


def test_the_full_reason_appears_once_not_in_every_gate_row(exported) -> None:
    """Found by reading the output.

    The whole exclusion paragraph was copied into each gate's detail as well as
    the banner, so one page carried it twice and ran two sentences together
    mid-cell. The row now says what it is and where the detail is.
    """
    html = exported["html"]
    for reason in exported["payload"]["scope_exclusions"].values():
        assert html.count(run_report._esc(reason)) == 1
    assert ".. " not in html


# --------------------------------------------------------------------------
# Nothing else regressed
# --------------------------------------------------------------------------


def test_the_stamp_rule_still_holds(exported) -> None:
    """These artifacts are real, so no stamp, and the run says measured."""
    assert exported["payload"]["data_provenance"] == run_report.PROVENANCE_MEASURED
    assert run_report.SYNTHETIC_STAMP not in exported["html"]


def test_the_html_is_self_contained(exported) -> None:
    html = exported["html"]
    for marker in set(CLI_MARKERS) | set(SERVER_MARKERS):
        assert marker not in html, marker


def test_the_measured_failure_is_still_the_headline(exported) -> None:
    """The reshaping must not have buried the one thing that was measured."""
    [establishment] = [
        g for g in exported["payload"]["gates"] if g["gate"] == "call_establishment"
    ]
    assert establishment["status"] == gates.FAIL
    assert exported["payload"]["verdict"]["status"] == gates.FAIL
    assert exported["result"].exit_code != 0


def test_the_run_identity_and_limits_survive(exported) -> None:
    html = exported["html"]
    assert "Run window (UTC)" in html
    assert "2026-08-01T20:28:18Z" in html
    assert "what THIS run did not measure" in html
