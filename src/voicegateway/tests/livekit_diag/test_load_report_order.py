"""The answer goes above the working.

The per-test table is the contracted deliverable: concurrency, duration,
establishment rate, peak CPU and memory, failures by cause. It sat BELOW the
gate detail, so on a real run a reader scrolled past 48 gate rows to reach the
three rows somebody actually asked for.

Order is now verdict, results, capacity, then the gate detail that produced
them. Capacity follows the results because it is derived from them.

**One thing outranks all of it.** When provenance is not measured, the synthetic
stamp is the first element in the body, and nothing goes above it. Its whole
design is that a reader scrolling to the numbers passes it on the way, and a
reordering that put results above it would defeat exactly that.
"""

from __future__ import annotations

import pytest

from voicegateway.livekit_diag import gates, run_report

MEASURED = {"id": "ramp-500", "artifact_sha256": "a" * 64}
UNMEASURED = {"id": "ramp-500", "artifact_sha256": None}

TESTS = [
    {
        "name": "ramp-20",
        "sequence": 0,
        "attempted_calls": 100,
        "succeeded_calls": 100,
        "peak_concurrency": 20,
        "duration_ms": 60000,
        "failures_by_cause": {},
    }
]


def _html(run=MEASURED, **kw) -> str:
    results = gates.node_cpu_gates(
        [gates.NodeUtilisationReading(node="box-1", utilisation=0.95, samples=12)]
    )
    return run_report.render_load_html(
        run_report.build_load_payload(
            run=run,
            tests=TESTS,
            gate_results=[g.as_dict() for g in results],
            **kw,
        )
    )


# The verdict block carries no heading, so it is located by the class the
# renderer gives it rather than by prose that does not exist.
VERDICT = 'class="verdict'


def _positions(html: str, *markers: str) -> list[int]:
    found = []
    for marker in markers:
        assert marker in html, marker
        found.append(html.index(marker))
    return found


# --------------------------------------------------------------------------
# The order
# --------------------------------------------------------------------------


def test_the_per_test_table_precedes_the_gate_detail() -> None:
    """The whole node. The contracted deliverable comes first."""
    tests_at, gates_at = _positions(_html(), "Per test", "<h2>Gates</h2>")
    assert tests_at < gates_at


def test_the_verdict_still_leads(html=None) -> None:
    """A reader needs the answer before the numbers behind it."""
    html = _html()
    verdict_at, tests_at = _positions(html, VERDICT, "Per test")
    assert verdict_at < tests_at


def test_capacity_follows_the_results_it_is_derived_from() -> None:
    capacity_at, tests_at, gates_at = _positions(
        _html(), "Capacity", "Per test", "<h2>Gates</h2>"
    )
    assert tests_at < capacity_at < gates_at


def test_the_limits_section_stays_last() -> None:
    """It qualifies everything above it, so it cannot precede any of it."""
    gates_at, limits_at = _positions(_html(), "<h2>Gates</h2>", "does not measure")
    assert gates_at < limits_at


def test_the_full_order_in_one_assertion() -> None:
    """Stated once, so a future reshuffle fails here rather than in five places."""
    order = _positions(
        _html(),
        VERDICT,
        "Per test",
        "Capacity",
        "<h2>Gates</h2>",
        "does not measure",
    )
    assert order == sorted(order), order


# --------------------------------------------------------------------------
# The stamp outranks the reordering
# --------------------------------------------------------------------------


def test_the_stamp_is_the_first_element_in_the_body() -> None:
    """The trap. Nothing goes above it, including the results."""
    html = _html(run=UNMEASURED)
    assert run_report.SYNTHETIC_STAMP in html
    body_at = html.index("<body>")
    stamp_at = html.index(run_report.SYNTHETIC_STAMP)
    for marker in ("Per test", VERDICT, "<h1>", "Capacity"):
        assert stamp_at < html.index(marker), marker
    # And it really is first, not merely early.
    assert html[body_at : body_at + len("<body>") + 200].find("stamp") != -1


def test_a_measured_run_carries_no_stamp_and_still_orders_correctly() -> None:
    """Non-vacuous: the stamp rule is not what is putting results first."""
    html = _html(run=MEASURED)
    assert run_report.SYNTHETIC_STAMP not in html
    tests_at, gates_at = _positions(html, "Per test", "<h2>Gates</h2>")
    assert tests_at < gates_at


@pytest.mark.parametrize("run", [MEASURED, UNMEASURED])
def test_the_order_holds_whatever_the_provenance(run) -> None:
    order = _positions(_html(run=run), "Per test", "Capacity", "<h2>Gates</h2>")
    assert order == sorted(order)


# --------------------------------------------------------------------------
# Nothing was lost in the move
# --------------------------------------------------------------------------


def test_every_section_is_still_present() -> None:
    """Reordering must not have dropped one on the way past."""
    html = _html()
    for section in (
        "Load-test run report",
        "Run window (UTC)",
        VERDICT,
        "Per test",
        "Capacity",
        "<h2>Gates</h2>",
        "Reproducible test assets",
        "does not measure",
        "Generated by voicegateway",
    ):
        assert section in html, section


def test_the_gate_rows_survive_the_move() -> None:
    """They moved down the page, not out of it. They are the evidence."""
    html = _html()
    assert "node_cpu" in html
    assert gates.FAIL in html
