"""The dashboard, and the guarantee that no panel outlives its series.

A hand-written dashboard drifts silently: the panel keeps its title, its axes
and its legend, and simply stops having data behind it. So this one is generated
from the live column sets, and these tests pin the two properties that makes
possible.

**No panel can name a series that does not exist**, because the generator checks
every one as it builds.

**A panel with no series is a note, never a graph.** An empty time-series panel
renders as a flat line at zero or a tidy "No data", and both read as *nothing
was happening* rather than *nobody measured this*.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voicegateway.loadtest import dashboard
from voicegateway.repository.node_samples_repository import (
    COUNTER_COLUMNS,
    GAUGE_COLUMNS,
)

CHECKED_IN = Path(__file__).resolve().parents[3].parent / "dashboards"


def _panels_by_type(kind: str) -> list[dict]:
    return [p for p in dashboard.build_dashboard()["panels"] if p["type"] == kind]


# --------------------------------------------------------------------------
# No panel outlives its series
# --------------------------------------------------------------------------


def test_every_graph_panel_names_only_real_columns() -> None:
    """The core guarantee. A graph exists only if its series does."""
    available = GAUGE_COLUMNS | COUNTER_COLUMNS
    for panel in dashboard.PANELS:
        if panel.missing:
            continue
        for column in panel.series:
            assert column in available, f"{panel.title} names a missing {column}"


def test_a_panel_whose_series_vanishes_becomes_a_note(monkeypatch) -> None:
    """Non-vacuous: prove the check actually fires.

    Drop a column from the live set and the panel that needed it must stop
    being a graph. Without this the guarantee could be true only by accident.
    """
    before = {p["title"] for p in _panels_by_type("timeseries")}
    assert "Goroutines" in before

    monkeypatch.setattr(
        dashboard, "GAUGE_COLUMNS", GAUGE_COLUMNS - {"go_goroutines"}, raising=True
    )
    after = _panels_by_type("timeseries")
    assert "Goroutines" not in {p["title"] for p in after}
    notes = [p for p in _panels_by_type("text") if p["title"].startswith("Goroutines")]
    assert notes, "the panel disappeared instead of becoming a note"
    assert "go_goroutines" in notes[0]["options"]["content"]


def test_the_panel_count_never_changes_only_the_panel_kind(monkeypatch) -> None:
    """A vanishing panel reads as a question nobody asked."""
    total = len(dashboard.build_dashboard()["panels"])
    monkeypatch.setattr(dashboard, "GAUGE_COLUMNS", set(), raising=True)
    assert len(dashboard.build_dashboard()["panels"]) == total


# --------------------------------------------------------------------------
# An empty graph is never rendered
# --------------------------------------------------------------------------


def test_unmeasured_panels_are_text_not_empty_graphs() -> None:
    unmeasured = [p for p in dashboard.PANELS if p.missing]
    assert unmeasured, "the fixture is wrong: nothing is unmeasured"
    titles = {p["title"] for p in _panels_by_type("text")}
    for panel in unmeasured:
        assert f"{panel.title} — NOT MEASURED" in titles


def test_every_unmeasured_panel_says_why_nothing_collects_it() -> None:
    """ "No data" without a reason is indistinguishable from a broken query."""
    for panel in dashboard.PANELS:
        if not panel.missing:
            continue
        assert panel.absent_note.strip(), f"{panel.title} has no reason"


def test_an_unmeasured_panel_states_it_is_not_a_zero() -> None:
    for note in _panels_by_type("text"):
        content = note["options"]["content"]
        assert "not a measurement of" in content
        assert "nothing here was checked" in content


def test_unmeasured_panels_occupy_a_full_panel_of_space() -> None:
    """Same footprint as a graph. A shrunken note reads as a minor aside."""
    graphs = _panels_by_type("timeseries")
    notes = _panels_by_type("text")
    assert graphs and notes
    size = (graphs[0]["gridPos"]["w"], graphs[0]["gridPos"]["h"])
    for note in notes:
        assert (note["gridPos"]["w"], note["gridPos"]["h"]) == size


def test_the_four_known_gaps_are_all_present_as_notes() -> None:
    """Named individually so silently dropping one is a test failure.

    Redis was the fifth until redis_exporter became a scrape source. It is not
    removed here to make anything pass: it is removed because it stopped being
    a gap, and a test pinning "X is unmeasured" has to be updated when X is
    measured or it starts preventing the improvement it was written to track.
    The panels below assert the other half, that Redis really is measured now.
    """
    titles = " ".join(p["title"] for p in _panels_by_type("text"))
    for gap in ("CPU per core", "RTP port", "ENA", "Conntrack"):
        assert gap in titles, gap
    assert "Redis" not in titles


def test_redis_is_measured_rather_than_a_note() -> None:
    """The other half of the change above, so the gap cannot quietly return."""
    measured = " ".join(p.get("title", "") for p in _panels_by_type("timeseries"))
    assert "Redis" in measured


# --------------------------------------------------------------------------
# Counter resets
# --------------------------------------------------------------------------


def test_counter_rates_go_null_across_a_reset_never_negative() -> None:
    """A restart rendered as a spike reads as an event rather than an artefact."""
    for panel in dashboard.PANELS:
        if not panel.sql or not any(c in COUNTER_COLUMNS for c in panel.series):
            continue
        assert "THEN NULL" in panel.sql, f"{panel.title} has no reset guard"
        assert "< 0" in panel.sql or "<= 0" in panel.sql


def test_gaps_are_drawn_as_gaps() -> None:
    """Connecting across a NULL invents the values underneath the line."""
    for panel in _panels_by_type("timeseries"):
        custom = panel["fieldConfig"]["defaults"]["custom"]
        assert custom["spanNulls"] is False


def test_a_division_never_divides_by_a_raw_zero() -> None:
    """NULLIF everywhere, so an idle denominator is unknown rather than infinite."""
    for panel in _panels_by_type("timeseries"):
        sql = panel["targets"][0]["rawSql"]
        if "/" not in sql:
            continue
        assert "NULLIF" in sql or "THEN NULL" in sql, panel["title"]


# --------------------------------------------------------------------------
# The checked-in file
# --------------------------------------------------------------------------


def test_the_checked_in_json_matches_the_generator() -> None:
    """Drift guard. A checked-in file that has drifted is a second source of
    truth, and the two disagreeing is exactly what generating it prevents."""
    path = CHECKED_IN / "voicegateway-load-test.json"
    assert path.is_file(), f"{path} is missing; regenerate it"
    assert path.read_text() == dashboard.dashboard_json(), (
        "dashboards/voicegateway-load-test.json is stale. Regenerate with "
        "voicegateway.loadtest.dashboard.dashboard_json()."
    )


def test_the_dashboard_is_valid_json_with_the_keys_grafana_needs() -> None:
    payload = json.loads(dashboard.dashboard_json())
    for key in ("title", "uid", "schemaVersion", "panels", "templating"):
        assert key in payload, key
    assert payload["panels"]


def test_the_datasource_is_templated_not_pinned_to_an_instance() -> None:
    """So the same file imports anywhere."""
    assert dashboard.DATASOURCE.startswith("${")
    for panel in _panels_by_type("timeseries"):
        assert panel["datasource"] == dashboard.DATASOURCE


def test_the_description_counts_what_is_actually_measured() -> None:
    """The header states the ratio, so a reader knows before scrolling."""
    measured = sum(1 for p in dashboard.PANELS if not p.missing)
    description = str(dashboard.build_dashboard()["description"])
    assert f"{measured} of {len(dashboard.PANELS)} panels" in description


@pytest.mark.parametrize("panel", dashboard.PANELS, ids=lambda p: p.title)
def test_every_panel_is_one_kind_or_the_other(panel: dashboard.Panel) -> None:
    """A measured panel needs SQL; an unmeasured one needs a reason."""
    if panel.missing:
        assert panel.absent_note.strip()
    else:
        assert panel.sql.strip(), f"{panel.title} is measured but has no query"


# --------------------------------------------------------------------------
# The SQL is executed, not merely inspected
# --------------------------------------------------------------------------


@pytest.fixture
async def seeded(tmp_path):
    """Six healthy scrapes, then a restart that sends the counters backwards."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlmodel import SQLModel

    from voicegateway.models.node_sample_model import NodeSample  # noqa: F401
    from voicegateway.repository.node_samples_repository import (
        NodeSampleInput,
        insert_samples,
    )

    t0 = 1_785_520_800_000
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'd.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(
            SQLModel.metadata.create_all,
            tables=[SQLModel.metadata.tables["node_samples"]],
        )
    rows, total, idle, req, acc = [], 1000.0, 800.0, 100, 99
    for i in range(6):
        rows.append(
            NodeSampleInput(
                node="sfu-1",
                source="node_exporter",
                at_ms=t0 + i * 60_000,
                outcome="ok",
                values={
                    "cpu_seconds_total": total,
                    "cpu_idle_seconds_total": idle,
                    "memory_total_bytes": 16_000_000_000,
                    "memory_available_bytes": 8_000_000_000,
                    "filefd_allocated": 4000 + i * 100,
                    "filefd_maximum": 524288,
                    "heap_inuse_bytes": 200_000_000,
                    "go_goroutines": 1500 + i * 10,
                    "rooms": 10 + i,
                    "participants": 20 + i,
                    "sip_calls_active": 5 + i,
                    "sip_invite_requests_total": req,
                    "sip_invite_accepted_total": acc,
                },
            )
        )
        total, idle, req, acc = total + 240.0, idle + 60.0, req + 100, acc + 99
    # The restart. Every counter is lower than the sample before it.
    rows.append(
        NodeSampleInput(
            node="sfu-1",
            source="node_exporter",
            at_ms=t0 + 7 * 60_000,
            outcome="ok",
            values={
                "cpu_seconds_total": 5.0,
                "cpu_idle_seconds_total": 4.0,
                "sip_invite_requests_total": 1,
                "sip_invite_accepted_total": 1,
            },
        )
    )
    async with AsyncSession(engine) as session:
        await insert_samples(session, rows)
        await session.commit()
    yield tmp_path / "d.db"
    await engine.dispose()


def _run(db_path, sql):
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        return conn.execute(sql).fetchall()


async def test_every_panel_query_actually_executes(seeded) -> None:
    """Shipping SQL nobody ran is shipping a panel nobody can use."""
    ran = 0
    for panel in dashboard.PANELS:
        if not panel.sql:
            continue
        assert _run(seeded, panel.sql), f"{panel.title} returned no rows"
        ran += 1
    assert ran == sum(1 for p in dashboard.PANELS if not p.missing)


async def test_a_restart_yields_null_and_never_a_negative_rate(seeded) -> None:
    """The reset rule, proven by running it rather than by matching a string.

    The last sample's counters are lower than the one before, which is what a
    restart looks like. That instant must be unknown. A negative rate would
    draw a spike, and a zero would draw an idle node at the moment it fell over.
    """
    for panel in dashboard.PANELS:
        if not panel.sql or not any(c in COUNTER_COLUMNS for c in panel.series):
            continue
        values = [row[2] for row in _run(seeded, panel.sql)]
        assert values[-1] is None, f"{panel.title} rendered the restart as a value"
        assert not [v for v in values if v is not None and v < 0], (
            f"{panel.title} produced a negative rate across the reset"
        )
        # Non-vacuous: the healthy samples in between did produce readings.
        assert [v for v in values if v is not None]


async def test_cpu_utilisation_computes_the_expected_fraction(seeded) -> None:
    """4 cpu-seconds/s accrued with 1 idle is 75% busy, and the query says so."""
    panel = next(p for p in dashboard.PANELS if p.title.startswith("CPU utilisation"))
    values = [row[2] for row in _run(seeded, panel.sql) if row[2] is not None]
    assert values
    for value in values:
        assert value == pytest.approx(0.75)
