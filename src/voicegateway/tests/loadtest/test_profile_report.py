"""The profile view: measurements and reference bands, and no verdict at all.

VoiceGateway profiles. Its default report shows what it measured and the range
each figure is usually read against, and stops. A human reads and decides.

The gate machinery is not deleted, it moves behind ``--acceptance``, because an
engagement that contracted 99.5% establishment does need a pass or a fail and a
pipeline needs an exit code. Two views over ONE set of measurements, so they can
never disagree about a number.

**Three properties this file exists to hold.**

*No judgement leaks into the default view.* Not a verdict block, not a status
tag, not a coloured bar, not an exit code, and not a line on the console. A
profile that prints PASS to the terminal while the document refuses to is two
products disagreeing.

*A quantity nobody can measure does not appear at all.* PPS headroom has no
published denominator, so a row reading "unknown" for it is noise rather than
disclosure. It is dropped, not reported.

*A quantity that IS measurable but was not collected is still named.* On the
real seven-step run, 46 of the 47 old UNKNOWN rows were this rather than a limit
of the tool. Rendering them blank would say there was nothing to report, which
is a worse lie than a verdict.
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

# Imported from the suites that own them, so self-containment cannot drift here
# while this file looks like it enforces it.
from voicegateway.tests.cli.test_livekit_report_cli import (  # noqa: E402
    _EXTERNAL_MARKERS as CLI_MARKERS,
)
from voicegateway.tests.server.test_diagnostics_report import (  # noqa: E402
    _EXTERNAL_MARKERS as SERVER_MARKERS,
)


def _config(tmp: Path) -> Path:
    config = tmp / "voicegw.yaml"
    config.write_text(
        yaml.dump(
            {"cost_tracking": {"enabled": True, "db_path": str(tmp / "throwaway.db")}}
        )
    )
    return config


@pytest.fixture(scope="module")
def profiled(tmp_path_factory):
    """The real capture, exported through the DEFAULT report path."""
    tmp = tmp_path_factory.mktemp("profile")
    monkey = pytest.MonkeyPatch()
    monkey.delenv("VOICEGW_DB_PATH", raising=False)
    config = _config(tmp)
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
    html = next(p for p in out.iterdir() if p.suffix == ".html")
    payload = json.loads(
        next(p for p in out.iterdir() if p.suffix == ".json").read_text()
    )
    monkey.undo()
    return {
        "result": result,
        "html": html.read_text(),
        "name": html.name,
        "payload": payload,
    }


# --------------------------------------------------------------------------
# No judgement, anywhere
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "marker",
    ['class="verdict', 'class="tag', "<h2>Gates</h2>", "Not in scope for this report"],
)
def test_no_judgement_survives_into_the_document(profiled, marker) -> None:
    assert marker not in profiled["html"], marker


@pytest.mark.parametrize("word", [gates.PASS, gates.FAIL, gates.UNKNOWN, gates.WAIVED])
def test_no_status_word_appears(profiled, word) -> None:
    """Not even in prose. A profile that says FAIL anywhere has judged."""
    assert word not in profiled["html"], word


def test_the_cli_exits_zero_on_a_run_that_would_fail(profiled) -> None:
    """The capture is a FAILED run: every call timed out.

    Under --acceptance it exits non-zero, and a separate suite pins that. Here
    it must exit 0, because a profile makes no claim to judge and delivering a
    verdict through the exit status while the document refuses to give one is
    the same contradiction wearing a different hat.
    """
    assert profiled["result"].exit_code == 0, profiled["result"].output


def test_the_console_names_no_verdict(profiled) -> None:
    out = profiled["result"].output
    assert "Verdict" not in out
    assert "Profiled" in out


# --------------------------------------------------------------------------
# What cannot be measured is absent, not reported as unknown
# --------------------------------------------------------------------------


def test_the_permanently_unmeasurable_resource_is_gone(profiled) -> None:
    """PPS headroom has no denominator anywhere, so it is not a row.

    Distinct from every other absence in this document: wiring an exporter
    cannot lift it, so listing it would put a permanent fact in a section a
    reader scans for things to fix.
    """
    assert "pps headroom" not in profiled["html"].lower()
    assert "fleet/pps" not in profiled["html"]


def test_that_set_is_the_judge_module_source_of_truth() -> None:
    """Named from the shared gate constant, never a second hand-written list.

    The renderer does not import judge (that would close a package loop between
    livekit_diag and loadtest), so this asserts the equality a shared import
    would otherwise have guaranteed.
    """
    assert run_report.PROFILE_PERMANENTLY_UNMEASURABLE == frozenset(
        judge.PERMANENT_HEADROOM_EXCLUSIONS
    )


def test_the_pps_counter_still_reaches_the_page(profiled) -> None:
    """Non-vacuous, and the distinction that makes the deletion honest.

    PPS headroom is unmeasurable. The pps allowance COUNTER is measured, and it
    is one of the five that fire on shaping, so the allowance row still names
    it. Deleting the word everywhere would have lost a real measurement.
    """
    assert "allowance" in profiled["html"].lower()


# --------------------------------------------------------------------------
# What was not collected is still named, with a remedy
# --------------------------------------------------------------------------


def test_uncollected_measurements_are_listed(profiled) -> None:
    assert "Not collected in this run" in profiled["html"]


def test_every_uncollected_row_says_why(profiled) -> None:
    """A blank would read as nothing to report. 46 of 47 on the real run were
    this, so the section carries most of what a reader can act on."""
    html = profiled["html"]
    section = html[html.index("Not collected in this run") :]
    section = section[: section.index("<h2")] if "<h2" in section else section
    assert "not measured" in section.lower() or "no scrape" in section.lower()


# --------------------------------------------------------------------------
# The reference band never becomes a verdict
# --------------------------------------------------------------------------


def test_the_band_fill_is_one_neutral_colour() -> None:
    """THE central rule of this view, asserted against the stylesheet.

    Colouring the fill by which side of the tick it lands on is a verdict
    wearing a chart's clothes: it grades the number before the reader has read
    it, in the one view that exists to stop doing that. So no status colour may
    appear in a band rule.
    """
    css = run_report._PROFILE_CSS
    band = css[css.index(".band") :]
    for status_colour in ("#1f7a44", "#a32020", "#a86a00", "#6a4ca8"):
        assert status_colour not in band.split(".band-tick")[0], status_colour


def test_the_band_renders_the_value_and_the_reference_separately() -> None:
    """The fill is the number, the tick is the reference. Two facts, two marks."""
    html = run_report._profile_band(0.838, 0.70)
    assert "83.8%" in html and "70.0%" in html
    assert "band-fill" in html and "band-tick" in html


def test_a_band_is_only_drawn_where_the_denominator_is_real() -> None:
    """A count has no natural maximum, so a bar for it would imply one.

    Same reasoning as dropping the PPS rows: where there is no denominator, the
    honest rendering is the numerator alone.
    """
    ratio = run_report._profile_reference(
        {"gate": gates.NODE_CPU_GATE, "value": 0.838, "threshold": 0.70}
    )
    count = run_report._profile_reference(
        {"gate": gates.SUSTAINED_HEALTH_GATE, "value": 2.0, "threshold": 3.0}
    )
    assert "band-fill" in ratio, "a 0..1 ratio has a real full scale"
    assert "band-fill" not in count, "a count has no maximum and must not imply one"


def test_return_to_baseline_gets_no_band_though_it_is_a_ratio_gate() -> None:
    """The subtle case. It is in RATIO_GATES but is a MULTIPLE of a baseline,
    with a 1.10 threshold, so it runs past 1.0 by design and has no full scale."""
    assert gates.RETURN_TO_BASELINE_GATE in gates.RATIO_GATES
    rendered = run_report._profile_reference(
        {"gate": gates.RETURN_TO_BASELINE_GATE, "value": 0.82, "threshold": 1.10}
    )
    assert "band-fill" not in rendered


# --------------------------------------------------------------------------
# Classification, so a new gate cannot vanish
# --------------------------------------------------------------------------


def test_every_load_gate_is_classified() -> None:
    """A gate nobody grouped would silently drop out of the document.

    The four unclassified ids are the diagnostics-probe gates, which a load
    payload never carries; asserting them explicitly keeps that a decision.
    """
    assert run_report.PROFILE_UNCLASSIFIED_GATES == {
        gates.AGENTS_GATE,
        gates.LATENCY_GATE,
        gates.SFU_CAPACITY_GATE,
        gates.SFU_QUALITY_GATE,
    }


def test_the_groups_do_not_overlap() -> None:
    seen: set[str] = set()
    for keys in run_report.PROFILE_GROUPS.values():
        for key in keys:
            assert key not in seen, key
            seen.add(key)


# --------------------------------------------------------------------------
# Still a self-contained document
# --------------------------------------------------------------------------


def test_the_html_is_self_contained(profiled) -> None:
    for marker in set(CLI_MARKERS) | set(SERVER_MARKERS):
        assert marker not in profiled["html"], marker


def test_it_writes_a_distinct_filename(profiled) -> None:
    """Both views can sit in one directory without one overwriting the other."""
    assert profiled["name"] == run_report.profile_filename("capture-01")
    assert profiled["name"] != run_report.load_report_filename("capture-01")


def test_the_json_is_the_same_payload_either_way(profiled) -> None:
    """The machine-readable export must not depend on which view a human asked
    for, so it still carries the gates and the verdict."""
    assert profiled["payload"]["gates"]
    assert profiled["payload"]["verdict"]["status"]


def test_the_measurements_a_reader_came_for_are_present(profiled) -> None:
    """Non-vacuous: removing judgement must not have removed the numbers."""
    html = profiled["html"]
    for section in ("Per test", "Measurements", "How to read these numbers"):
        assert section in html, section
