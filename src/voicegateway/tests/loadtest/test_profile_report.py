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


def test_uncollected_measurements_are_named_as_a_count(profiled) -> None:
    """Named, and counted, but not tabled.

    This assertion used to read "every uncollected ROW says why", which was the
    right contract while the rows were in the HTML. They are not: one row per
    (subject x measurement) pair is 46 rows on this seven-step run and 111 on a
    thirteen-target fleet, and a reader scanning for what the fleet did had to
    scroll past all of them. The per-row reason did not disappear, it moved to
    the JSON, which is asserted below.
    """
    html = profiled["html"]
    section = html[html.index("Not collected in this run") :]
    section = section[: section.index("<h2")]
    assert "were not collected" in section
    assert str(len(profiled["payload"]["not_collected"])) in section


def test_the_uncollected_section_renders_no_table_and_no_rows(profiled) -> None:
    """The whole point of the change, asserted as absence.

    Every gap on this run is run-specific, so the section must be prose only. A
    single placeholder row would put the reader back in the list they were being
    kept out of.
    """
    html = profiled["html"]
    section = html[html.index("Not collected in this run") :]
    section = section[: section.index("<h2")]
    assert "<table" not in section
    assert "<tr" not in section
    assert "<li" not in section


def test_every_uncollected_measurement_still_says_why_in_the_json(profiled) -> None:
    """Moved, not dropped. The JSON carries strictly more than the table did.

    46 of the 47 old UNKNOWN rows on this run were measurable-but-uncollected,
    so this list is most of what a reader can act on and losing it would be a
    far worse trade than the noise it removes.
    """
    entries = profiled["payload"]["not_collected"]
    assert entries
    for entry in entries:
        assert entry["measurement"]
        assert entry["cause"]
        assert entry["change"]
        # Verbatim, so a misfiled cause cannot hide what the run recorded.
        assert entry["recorded"]


def test_uncollected_is_null_when_nothing_was_gated_never_empty() -> None:
    """An ungated run has an UNKNOWN gap list, not an empty one.

    Flattening the two would make a run nobody judged look like a run with
    nothing missing, which is the same absent-is-not-zero rule the values
    follow.
    """
    assert run_report.not_collected_entries(None) is None
    assert run_report.not_collected_entries([]) == []


def test_a_permanently_unmeasurable_gap_is_not_in_the_uncollected_list(
    profiled,
) -> None:
    """The distinction the change exists to protect.

    A run-specific gap is a thing to go and fix. A permanent one is a fact about
    the world, and putting it in the same list turns that list into a queue that
    never empties.
    """
    subjects = " ".join(str(e["subject"]) for e in profiled["payload"]["not_collected"])
    assert "pps" not in subjects


def test_the_permanent_exclusions_keep_their_footnote(profiled) -> None:
    """Dropping the rows must not shrink the disclosure.

    It moved to the structural-limits section, where a fact no run can change
    belongs, and it still counts what was dropped so it cannot go quiet.
    """
    html = profiled["html"]
    section = html[html.index("What this report structurally does not measure") :]
    assert "absent from this document entirely" in section
    assert "no denominator" in section


# --------------------------------------------------------------------------
# At a glance: one table, every line carrying a number
# --------------------------------------------------------------------------


def _glance(html: str) -> str:
    start = html.index("At a glance")
    return html[start : html.index("<h2", start + 1)]


def test_the_glance_table_is_the_first_thing_after_the_header(profiled) -> None:
    """A reader who stops after one table has to have read the answer."""
    html = profiled["html"]
    assert html.index("At a glance") < html.index("Per test")
    assert html.index("At a glance") < html.index("Measurements")


def test_every_glance_line_carries_a_measured_number(profiled) -> None:
    """THE rule of this table. One placeholder breaks the claim for all of them.

    A row that would read "unknown" is omitted entirely rather than rendered
    with a dash or an empty cell, because the whole reason a reader can scan
    this in ten seconds is that there is nothing in it to skip over.
    """
    section = _glance(profiled["html"])
    assert "<tr>" in section
    for placeholder in ("not measured", "not recorded", "unknown", "&mdash;"):
        assert placeholder not in section.lower(), placeholder


def test_the_reading_is_only_ever_ok_or_over(profiled) -> None:
    import re

    readings = set(re.findall(r"<td class='reading'>([^<]*)</td>", profiled["html"]))
    assert readings, "no reading cell rendered at all"
    assert readings <= {"ok", "over", ""}


def test_the_reading_is_never_styled_like_a_gate() -> None:
    """A chip with a colour is a verdict wearing different clothes.

    Same reasoning as the band fill, and asserted the same way: against the
    stylesheet, so adding a status colour to the rule fails here rather than in
    a screenshot nobody takes.
    """
    css = run_report._PROFILE_CSS
    rule = css[css.index(".reading") :]
    rule = rule.split("}")[0]
    for status_colour in ("#1f7a44", "#a32020", "#a86a00", "#6a4ca8"):
        assert status_colour not in rule, status_colour
    for chip in ("background", "border", "border-radius", "font-weight"):
        assert chip not in rule, chip


def test_the_reading_comes_from_the_gate_and_not_from_a_second_comparison(
    profiled,
) -> None:
    """The safety property: the glance can never say ok where a gate said no.

    Several gates do not compare their value against their threshold at all.
    resource_trend carries a signed drift in the metric's own units against a
    threshold that is a fraction of that metric's baseline, so a renderer doing
    its own arithmetic would grade it wrongly and disagree with the row below.
    """
    over_keys = {
        run_report._profile_key(g)
        for g in profiled["payload"]["gates"]
        if g["status"] == gates.FAIL and g.get("value") is not None
    }
    assert over_keys, "the fixture is wrong: nothing failed"
    rows = {
        row["what"]: row
        for row in run_report._glance_rows(
            profiled["payload"], run_report._profile_rows(profiled["payload"])[0]
        )
    }
    checked = 0
    for key in over_keys:
        name = run_report._GLANCE_NAMES.get(key)
        if name is None or name not in rows:
            continue
        assert rows[name]["reading"] == "over", key
        checked += 1
    # Or the loop above proves nothing: every key skipping would pass silently.
    assert checked, f"no over-reading key reached the glance table: {over_keys}"


def test_a_waived_gate_gets_no_reading_rather_than_a_favourable_one() -> None:
    """A waiver removes the comparison. Re-applying it here would undo that."""
    assert gates.WAIVED not in run_report._GLANCE_READINGS
    assert run_report._GLANCE_READINGS == {gates.PASS: "ok", gates.FAIL: "over"}


def test_the_worst_subject_is_quoted_never_an_average() -> None:
    """Worst-node semantics, which the contracted criteria are written in.

    A tier healthy on average can contain one node that breached, and averaging
    is how that node stops being visible.
    """
    payload = {
        "tests": [],
        "gates": [
            {
                "gate": gates.NODE_CPU_GATE,
                "status": gates.PASS,
                "subject": "quiet",
                "value": 0.10,
                "threshold": 0.70,
            },
            {
                "gate": gates.NODE_CPU_GATE,
                "status": gates.FAIL,
                "subject": "busy",
                "value": 0.90,
                "threshold": 0.70,
            },
        ],
    }
    rows = run_report._glance_rows(payload, run_report._profile_rows(payload)[0])
    cpu = next(r for r in rows if r["what"] == "Peak CPU, worst node")
    assert "90" in cpu["value"] and "busy" in cpu["why"]
    assert cpu["reading"] == "over"


def test_drift_is_summarised_as_a_count_because_its_units_differ() -> None:
    """Bytes and socket counts have no common scale, so there is no worst one.

    Quoting a "largest" drift across metrics would just be picking whichever one
    happens to be measured in big numbers.
    """
    payload = {
        "tests": [],
        "gates": [
            {
                "gate": gates.RESOURCE_TREND_GATE,
                "status": gates.FAIL,
                "subject": "n/memory_used_bytes",
                "value": 45_800_000.0,
                "threshold": 0.01,
            },
            {
                "gate": gates.RESOURCE_TREND_GATE,
                "status": gates.PASS,
                "subject": "n/sockstat_udp_inuse",
                "value": 4.0,
                "threshold": 0.01,
            },
        ],
    }
    rows = run_report._glance_rows(payload, run_report._profile_rows(payload)[0])
    drift = next(r for r in rows if r["what"].startswith("Resource drift"))
    assert drift["value"] == "1 of 2"
    assert "Measured resources drifting" in drift["why"]
    assert drift["reading"] == "over"
    assert "45" not in drift["value"], "a byte drift was quoted as the worst"


def test_a_measurement_nobody_ordered_still_reaches_the_table() -> None:
    """A number that vanishes because nobody added it to a display list is the
    exact failure this file is written against."""
    payload = {
        "tests": [],
        "gates": [
            {
                "gate": gates.SFU_CAPACITY_GATE,
                "status": gates.PASS,
                "subject": "sfu",
                "value": 1.0,
                "threshold": 1.0,
            }
        ],
    }
    assert gates.SFU_CAPACITY_GATE not in run_report._GLANCE_ORDER
    rows = run_report._glance_rows(payload, run_report._profile_rows(payload)[0])
    assert len(rows) == 1


#: A run that measured something in both directions, which the capture fixture
#: does not: every one of its calls timed out, so it sent no RTP and no node
#: gate ever evaluated. Built here rather than added to the fixture, because the
#: fixture's value is that it is a real failed capture.
_TWO_SIDED = {
    "tests": [
        {"name": "soak", "rtp_packets_sent": 111_682, "rtp_packets_received": 110_698},
        {"name": "ramp", "rtp_packets_sent": 1_498, "rtp_packets_received": 1_400},
    ],
    "gates": [
        {
            "gate": gates.NODE_CPU_GATE,
            "status": gates.PASS,
            "subject": "sip-1",
            "value": 0.66,
            "threshold": gates.MAX_NODE_CPU_UTILISATION,
        },
    ],
}


def test_two_way_media_is_reported_from_the_rtp_totals() -> None:
    """Measured since the first import and rendered nowhere until now.

    It is the run's only evidence that audio came BACK. Labelled as a run total
    rather than as a per-call figure, because nothing attributes a packet to a
    call and the report says so elsewhere.
    """
    rows = run_report._glance_rows(_TWO_SIDED, run_report._profile_rows(_TWO_SIDED)[0])
    media = next(r for r in rows if r["what"] == "Two-way media")
    # The worse of the two, not the flattering one, and it names which test.
    assert media["value"].startswith("0.935")
    assert "ramp" in media["why"]
    assert "not that every call carried it" in media["why"]


def test_the_test_name_in_the_media_row_is_escaped() -> None:
    """It comes off disk, as the imported artifacts' directory name.

    The glance renderer inserts `why` raw, because every other producer of that
    field escapes its own interpolations. This one is the exception that has to
    keep the rule.
    """
    media = run_report._glance_media_row(
        {
            "tests": [
                {
                    "name": "<script>alert(1)</script>",
                    "rtp_packets_sent": 10,
                    "rtp_packets_received": 9,
                }
            ]
        }
    )
    assert media is not None
    assert "<script>" not in media["why"]
    assert "&lt;script&gt;" in media["why"]


def test_a_zero_denominator_produces_no_media_row_rather_than_a_zero() -> None:
    """The capture fixture is exactly this: every call timed out, nothing sent.

    0 sent is not 0.0 received-per-sent, it is no ratio at all, and rendering it
    as a figure would put a fabricated number in the one table that promises
    every line carries a measured one.
    """
    assert (
        run_report._glance_media_row(
            {"tests": [{"name": "t", "rtp_packets_sent": 0, "rtp_packets_received": 0}]}
        )
        is None
    )


def test_no_reference_means_two_blank_cells_never_an_invented_one() -> None:
    """Nobody contracted a threshold for two-way media, so this document does
    not supply one. Filling the column would be it grading on its own
    authority."""
    media = run_report._glance_media_row(_TWO_SIDED)
    assert media is not None
    assert media["reference"] == ""
    assert media["reading"] == ""


def test_the_reference_carries_its_direction(profiled) -> None:
    """ "70%" alone leaves the reader to remember which side is safe."""
    assert "at least 99.5%" in _glance(profiled["html"])
    rows = run_report._glance_rows(_TWO_SIDED, run_report._profile_rows(_TWO_SIDED)[0])
    cpu = next(r for r in rows if r["what"] == "Peak CPU, worst node")
    assert cpu["reference"] == "at most 70%"


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


class TestPerCallMediaCountsReachThePayload:
    """The counts the two-way media gate judges must be in the row it judged.

    Shipped without this once. The gate reported the correct verdict and said
    "all 900 answered calls received audio back", while the test row it came
    from carried null for both counts. The verdict was right and unverifiable,
    which is the combination this whole report exists to avoid.
    """

    def test_the_counts_are_carried_through(self) -> None:
        row = run_report._load_test_row(
            {
                "name": "g0",
                "attempted_calls": 900,
                "succeeded_calls": 900,
                "calls_answered_with_inbound": 899,
                "calls_answered_without_inbound": 1,
            }
        )
        assert row["calls_answered_with_inbound"] == 899
        assert row["calls_answered_without_inbound"] == 1

    def test_absent_counts_stay_none_and_never_become_zero(self) -> None:
        # Zero silent calls is a pass. Not having counted them is not, and a
        # row that renders None as 0 turns the second into the first.
        row = run_report._load_test_row({"name": "g0", "attempted_calls": 10})
        assert row["calls_answered_with_inbound"] is None
        assert row["calls_answered_without_inbound"] is None

    def test_a_measured_zero_stays_zero(self) -> None:
        # THE case that matters most, and the one the other tests did not pin.
        # Zero silent calls is the passing result. If it came through as None
        # the gate would read the run as never counted and return UNKNOWN, so
        # a clean run would report as unmeasured.
        row = run_report._load_test_row(
            {
                "name": "g0",
                "attempted_calls": 900,
                "succeeded_calls": 900,
                "calls_answered_with_inbound": 900,
                "calls_answered_without_inbound": 0,
            }
        )
        assert row["calls_answered_without_inbound"] == 0
        assert row["calls_answered_without_inbound"] is not None

    def test_persisted_strings_become_integers(self) -> None:
        # The row arrives from a database driver, not from this process. A
        # count that came back as text would otherwise reach a gate that
        # compares it against 0 with >, and "12" > 0 is a TypeError rather
        # than a verdict.
        row = run_report._load_test_row(
            {
                "name": "g0",
                "calls_answered_with_inbound": "899",
                "calls_answered_without_inbound": "1",
            }
        )
        assert row["calls_answered_with_inbound"] == 899
        assert row["calls_answered_without_inbound"] == 1

    def test_a_silent_run_shows_its_count_in_the_row(self) -> None:
        # The 24 hour soak: 16,606 answered calls with audio and 12,198
        # without, on a run whose establishment ratio was 1.0.
        row = run_report._load_test_row(
            {
                "name": "soak",
                "attempted_calls": 28804,
                "succeeded_calls": 28804,
                "calls_answered_with_inbound": 16606,
                "calls_answered_without_inbound": 12198,
            }
        )
        assert row["establishment_ratio"] == 1.0
        assert row["calls_answered_without_inbound"] == 12198
