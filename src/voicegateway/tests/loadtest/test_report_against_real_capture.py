"""The deliverable itself, rendered from the one real capture and read.

Both fixes are asserted here against the artifact a client's run actually
produces, rather than against a hand-built payload: a threshold that renders
correctly in a unit test and wrong in the exported file has not been fixed.

The third assertion set came from READING the output. The gates table explained
one absence two incompatible ways on the same page, telling a reader that no
node was sampled in the window and, three rows down, that the test had no
window. Both cannot be true, and a client who notices stops trusting the rest.
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
from voicegateway.livekit_diag.run_report import _ratio_pct

runner = CliRunner()

CAPTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "loadtest" / "capture-01"
)

# Imported from the tests that own them, so this cannot drift from the real
# self-containment rules while looking like it enforces them.
from voicegateway.tests.cli.test_livekit_report_cli import (  # noqa: E402
    _EXTERNAL_MARKERS as CLI_MARKERS,
)
from voicegateway.tests.server.test_diagnostics_report import (  # noqa: E402
    _EXTERNAL_MARKERS as SERVER_MARKERS,
)


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    """Import the real capture and export both artifacts, into a throwaway db."""
    tmp = tmp_path_factory.mktemp("r3")
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
    assert imported.exit_code == 0, imported.output
    out = tmp / "out"
    result = runner.invoke(
        app,
        [
            "loadtest",
            "report",
            "--acceptance",
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
# The thresholds, as the exported file states them
# --------------------------------------------------------------------------


def test_the_contracted_thresholds_appear_as_percentages(exported) -> None:
    """The two that were misstated, plus the two that shared the render site."""
    html = exported["html"]
    for constant in (
        gates.MIN_ESTABLISHMENT_RATIO,
        gates.MAX_NODE_CPU_UTILISATION,
        gates.MAX_NODE_MEMORY_UTILISATION,
        gates.MIN_HEADROOM_FRACTION,
    ):
        assert _ratio_pct(constant) in html, constant


def test_the_old_rounded_forms_are_gone(exported) -> None:
    """ ">1.0<" and ">0.8<" were what a client read where the contract said
    99.5% and 75%."""
    for cell in (">1.0</td>", ">0.8</td>"):
        assert cell not in exported["html"], cell


def test_the_html_and_the_json_agree_on_every_threshold(exported) -> None:
    """The two exported artifacts contradicted each other. Checked per gate."""
    for gate in exported["payload"]["gates"]:
        threshold = gate.get("threshold")
        if threshold is None:
            continue
        # Not every gate's threshold is a fraction. sustained_health carries a
        # COUNT of consecutive failed samples, and rendering 3 as "300%" is the
        # exact misstatement RATIO_GATES exists to prevent, so it is checked as
        # a count rather than exempted.
        if gate["gate"] not in gates.RATIO_GATES:
            assert gate["gate"] in gates.ALL_GATES, gate["gate"]
            assert str(int(threshold)) in exported["html"], gate
            # And never as a percentage, which is the misstatement RATIO_GATES
            # exists to prevent. Only checked for a non-zero threshold: the
            # percent form of 0 is "0%", which collides with the stylesheet's
            # own "width: 100%" and would fail on every report regardless of
            # how the gate rendered.
            if threshold:
                assert _ratio_pct(threshold) not in exported["html"], gate
            continue
        assert _ratio_pct(threshold) in exported["html"], gate


# --------------------------------------------------------------------------
# No other product's limitations
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase",
    [
        "the prober's own message",
        "SFU data channel",
        "billed trial calls",
        "agents check",
        "the host that ran the probe",
    ],
)
def test_no_probe_only_phrase_appears(exported, phrase) -> None:
    """Compared through the renderer's own escaping, or it matches nothing."""
    assert run_report._esc(phrase) not in exported["html"]


def test_the_limits_still_name_what_this_run_did_not_measure(exported) -> None:
    """Emptying the section would be worse than the defect it replaced."""
    joined = " ".join(exported["payload"]["not_measured"]).lower()
    for subject in (
        "rtp-port headroom",
        "network headroom",
        "packet loss",
        "time window overlap",
        "worst node",
        "calls-per-node",
        "one vantage point",
    ):
        assert subject in joined, subject


# --------------------------------------------------------------------------
# Found by reading: one absence, one explanation
# --------------------------------------------------------------------------


def test_the_report_explains_the_missing_samples_one_way(exported) -> None:
    """It said "no node was sampled in the window" and "the test has no window".

    Both on the same page, about the same run. A client who reads carefully sees
    a document contradicting itself and stops trusting the numbers above it.
    """
    html = exported["html"]
    assert "no node was sampled in the window" in html
    assert "the test has no window" not in html


def test_every_unknown_gate_says_why(exported) -> None:
    """UNKNOWN with no reason is indistinguishable from a gate that broke."""
    unknown = [g for g in exported["payload"]["gates"] if g["status"] == gates.UNKNOWN]
    assert unknown
    for gate in unknown:
        assert gate["detail"], gate
        assert (
            "not measured" in gate["detail"]
            or "no node was sampled" in (gate["detail"])
        ), gate["detail"]


# --------------------------------------------------------------------------
# Still a deliverable
# --------------------------------------------------------------------------


def test_the_run_is_measured_and_unstamped(exported) -> None:
    payload = exported["payload"]
    assert payload["data_provenance"] == run_report.PROVENANCE_MEASURED
    assert run_report.SYNTHETIC_STAMP not in exported["html"]


def test_the_html_is_self_contained(exported) -> None:
    """Checked against the marker lists the two owning tests already use."""
    html = exported["html"]
    for marker in set(CLI_MARKERS) | set(SERVER_MARKERS):
        assert marker not in html, marker


def test_the_cli_still_exits_non_zero(exported) -> None:
    assert exported["result"].exit_code != 0


def test_no_number_reads_as_a_fabricated_zero(exported) -> None:
    """The unmeasured cells must say so rather than showing 0."""
    html = exported["html"]
    assert html.count('class="nm"') >= 4
    # The establishment value IS a measured zero and must NOT be suppressed.
    assert re.search(r">0%</td>", html), "a measured 0% was rendered as absent"
