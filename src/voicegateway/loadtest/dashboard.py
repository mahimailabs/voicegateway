"""A Grafana dashboard built FROM the series ``node_samples`` actually carries.

The dashboard JSON is generated rather than hand-written, and generated against
the live column sets in :mod:`voicegateway.repository.node_samples_repository`.
That is the whole design. A hand-written dashboard drifts the moment a column is
renamed, and it drifts SILENTLY: the panel keeps its title, its axes and its
legend, and simply stops having data behind it.

An empty graph is a lie
-----------------------

A time-series panel with no series behind it renders as a flat line at zero, or
as an empty pane with a tidy "No data" in the corner. Both read as *nothing was
happening*. Neither is what actually happened, which is *nobody measured this*.

So a panel whose series this system does not collect is emitted as a TEXT panel
that says so, in the same grid position a graph would have occupied. It takes up
the same space, it is impossible to miss, and it cannot be mistaken for a quiet
hour on a healthy fleet.

Four of the panels an operator would want are in that state today: per-core CPU,
RTP port usage, ENA packet-per-second allowances and conntrack occupancy.
Nothing scrapes any of them. Listing them as unmeasured is more useful than
omitting them, because an absent panel reads as a question nobody asked.

Redis was the fifth and no longer is. redis_exporter is a scrape source, so
reachability, memory against its limit and blocked clients are measured panels
and the acceptance criterion has an answer instead of a note saying nobody
looked.

Counter resets
--------------

Rates are computed in SQL with a window function, and a negative delta yields
NULL rather than a negative rate. A counter that went backwards is a restart,
and the rule matches
:func:`voicegateway.repository.node_samples_repository.counter_rates`: a reset is
unknown, never zero, because rendering a restart as an idle node is a clean bill
of health at exactly the moment something fell over.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from voicegateway.repository.node_samples_repository import (
    COUNTER_COLUMNS,
    GAUGE_COLUMNS,
)

#: Grafana datasource variable. Templated rather than pinned to a uid, so the
#: same file imports into any instance.
DATASOURCE = "${DS_VOICEGATEWAY}"

#: The table every panel reads. One table, because correlating across two would
#: mean joining on timestamps that were never guaranteed to align.
TABLE = "node_samples"

_PANEL_W = 12
_PANEL_H = 8


@dataclass(frozen=True)
class Panel:
    """One panel, and the series it needs to exist.

    ``series`` is checked against the live column sets when the dashboard is
    built. A panel naming a column ``node_samples`` does not carry is emitted as
    an unmeasured note instead of a graph, which is the one behaviour this
    module exists to guarantee.
    """

    title: str
    series: tuple[str, ...]
    description: str
    # SQL for the measured case, with {table} and {cols} left to format.
    sql: str = ""
    # Why nothing collects this, shown when the series is absent. Required for
    # a panel that can be unmeasured, because "no data" without a reason is
    # indistinguishable from a broken query.
    absent_note: str = ""
    unit: str = "short"

    @property
    def missing(self) -> tuple[str, ...]:
        available = GAUGE_COLUMNS | COUNTER_COLUMNS
        return tuple(c for c in self.series if c not in available)


def _rate(column: str) -> str:
    """A per-second rate from a cumulative column, NULL across a reset.

    The ``CASE`` is the reset rule. Without it a restarted counter renders as a
    large negative spike, and a reader's eye reads a spike as an event rather
    than as an artefact.
    """
    return (
        f"CASE WHEN {column} - LAG({column}) OVER w < 0 THEN NULL "
        f"ELSE ({column} - LAG({column}) OVER w) * 1000.0 "
        "/ NULLIF(at_ms - LAG(at_ms) OVER w, 0) END"
    )


PANELS: tuple[Panel, ...] = (
    Panel(
        title="Establishment rate",
        series=("sip_invite_accepted_total", "sip_invite_requests_total"),
        description=(
            "Accepted INVITEs over requested, from the SIP node's own counters. "
            "NULL across a counter reset rather than a spike. This is the "
            "node's view; the authoritative establishment figure for a test "
            "comes from the load generator's own counts."
        ),
        sql=(
            "SELECT at_ms AS time, node, "
            "CASE WHEN sip_invite_requests_total - LAG(sip_invite_requests_total) "
            "OVER w <= 0 THEN NULL ELSE "
            "1.0 * (sip_invite_accepted_total - LAG(sip_invite_accepted_total) OVER w) "
            "/ (sip_invite_requests_total - LAG(sip_invite_requests_total) OVER w) "
            "END AS establishment "
            f"FROM {TABLE} WINDOW w AS (PARTITION BY node, source ORDER BY at_ms) "
            "ORDER BY at_ms"
        ),
        unit="percentunit",
    ),
    Panel(
        title="CPU utilisation per node",
        series=("cpu_seconds_total", "cpu_idle_seconds_total"),
        description=(
            "1 - idle/total. cpu_seconds_total is summed over every cpu and "
            "every mode, so its rate is the core count and this needs no "
            "assumption about machine size. NULL across a reset."
        ),
        sql=(
            "SELECT at_ms AS time, node, "
            f"1.0 - ({_rate('cpu_idle_seconds_total')}) "
            f"/ NULLIF({_rate('cpu_seconds_total')}, 0) AS cpu "
            f"FROM {TABLE} WINDOW w AS (PARTITION BY node, source ORDER BY at_ms) "
            "ORDER BY at_ms"
        ),
        unit="percentunit",
    ),
    Panel(
        title="CPU per core",
        series=("cpu_per_core",),
        description="Per-core utilisation, to spot a single saturated core.",
        absent_note=(
            "Not collected. cpu_seconds_total is scraped already summed across "
            "cpus and modes, so the per-core breakdown is gone before it "
            "reaches this system. A node pinning one core while its average "
            "looks healthy would not show here."
        ),
        unit="percentunit",
    ),
    Panel(
        title="Memory utilisation per node",
        series=("memory_available_bytes", "memory_total_bytes"),
        description=(
            "Used over total, both read from the same row so the two halves "
            "describe the same instant. Peak utilisation is where AVAILABLE is "
            "lowest."
        ),
        sql=(
            "SELECT at_ms AS time, node, "
            "1.0 - (1.0 * memory_available_bytes / NULLIF(memory_total_bytes, 0)) "
            f"AS memory FROM {TABLE} ORDER BY at_ms"
        ),
        unit="percentunit",
    ),
    Panel(
        title="File descriptors against the limit",
        series=("filefd_allocated", "filefd_maximum"),
        description=(
            "Allocated over maximum. The acceptance threshold is 20% remaining, "
            "so this panel is read against 0.80."
        ),
        sql=(
            "SELECT at_ms AS time, node, "
            "1.0 * filefd_allocated / NULLIF(filefd_maximum, 0) AS fd_used "
            f"FROM {TABLE} ORDER BY at_ms"
        ),
        unit="percentunit",
    ),
    Panel(
        title="RTP port usage",
        series=("rtp_ports_in_use",),
        description="Media ports in use against the configured range.",
        absent_note=(
            "Not collected. Nothing scrapes the media port range, so the "
            "port-exhaustion half of the headroom requirement is unevaluated "
            "rather than passing. A run can clear every CPU and memory ceiling "
            "and still have been near this limit."
        ),
    ),
    Panel(
        title="ENA packets per second allowance",
        series=("ena_pps_allowance_exceeded",),
        description="Network allowance exceedances on the instance's interface.",
        absent_note=(
            "Not collected. No interface saturation or allowance counter is "
            "scraped, so network headroom is unmeasured. This is the panel that "
            "would show a link limit reached while every host metric looked fine."
        ),
    ),
    Panel(
        title="Conntrack occupancy",
        series=("nf_conntrack_entries",),
        description="Tracked connections against the table maximum.",
        absent_note=(
            "Not collected. Conntrack exhaustion drops new flows while CPU and "
            "memory stay unremarkable, which is exactly the failure this panel "
            "would have caught."
        ),
    ),
    Panel(
        title="Go heap in use",
        series=("heap_inuse_bytes",),
        description=(
            "Heap actually in use, NOT RSS. Go returns memory to the OS lazily, "
            "so RSS lags and a return-to-baseline check on it reads as a leak."
        ),
        sql=(
            f"SELECT at_ms AS time, node, heap_inuse_bytes FROM {TABLE} ORDER BY at_ms"
        ),
        unit="bytes",
    ),
    Panel(
        title="Goroutines",
        series=("go_goroutines",),
        description=(
            "A count that does not return to its idle level after teardown is "
            "the clearest signal of a leaked session."
        ),
        sql=f"SELECT at_ms AS time, node, go_goroutines FROM {TABLE} ORDER BY at_ms",
    ),
    Panel(
        title="Rooms, participants and active calls",
        series=("rooms", "participants", "sip_calls_active"),
        description=(
            "Read for what does NOT drain after a run. A count that stays up "
            "once traffic stops is a stale room or session, and that is the "
            "shape a leak takes here."
        ),
        sql=(
            "SELECT at_ms AS time, node, rooms, participants, sip_calls_active "
            f"FROM {TABLE} ORDER BY at_ms"
        ),
    ),
    Panel(
        title="Redis reachability",
        series=("redis_up",),
        description=(
            "1 when the exporter reached Redis on that scrape, 0 when it ran "
            "and could not. A gap is neither: it means nobody scraped, and the "
            "line breaks rather than dropping to zero."
        ),
        sql=(
            "SELECT at_ms AS time, node, redis_up AS up "
            f"FROM {TABLE} ORDER BY at_ms"
        ),
        unit="short",
    ),
    Panel(
        title="Redis memory against its limit",
        series=("redis_memory_used_bytes", "redis_memory_max_bytes"),
        description=(
            "Used over the configured maximum. Redis with no maxmemory reports "
            "a maximum of 0, which is no limit rather than no memory, so those "
            "rows are excluded here instead of rendering as full."
        ),
        sql=(
            "SELECT at_ms AS time, node, "
            "1.0 * redis_memory_used_bytes / NULLIF(redis_memory_max_bytes, 0) "
            f"AS memory_used FROM {TABLE} ORDER BY at_ms"
        ),
        unit="percentunit",
    ),
    Panel(
        title="Redis blocked clients",
        series=("redis_blocked_clients",),
        description=(
            "Clients waiting on a blocking command right now. Rises before a "
            "sustained failure rather than after it."
        ),
        sql=(
            "SELECT at_ms AS time, node, redis_blocked_clients AS blocked "
            f"FROM {TABLE} ORDER BY at_ms"
        ),
        unit="short",
    ),
)


def _grid(index: int) -> dict[str, int]:
    """Two panels per row, in declaration order."""
    return {
        "h": _PANEL_H,
        "w": _PANEL_W,
        "x": (index % 2) * _PANEL_W,
        "y": (index // 2) * _PANEL_H,
    }


def _unmeasured_panel(panel: Panel, index: int) -> dict[str, object]:
    """A text panel occupying the space a graph would have.

    Same footprint on purpose. A missing panel reads as a question nobody
    asked, and a shrunken one reads as a minor note.
    """
    missing = ", ".join(panel.missing)
    return {
        "type": "text",
        "title": f"{panel.title} — NOT MEASURED",
        "gridPos": _grid(index),
        "options": {
            "mode": "markdown",
            "content": (
                f"### Not measured\n\n**{panel.title}** would need "
                f"`{missing}`, which `{TABLE}` does not carry.\n\n"
                f"{panel.absent_note}\n\n"
                "_This is an absence of measurement, not a measurement of "
                "zero. Nothing here passed; nothing here was checked._"
            ),
        },
    }


def _timeseries_panel(panel: Panel, index: int) -> dict[str, object]:
    return {
        "type": "timeseries",
        "title": panel.title,
        "description": panel.description,
        "gridPos": _grid(index),
        "datasource": DATASOURCE,
        "fieldConfig": {
            "defaults": {
                "unit": panel.unit,
                # Gaps are drawn as gaps. Connecting across a NULL would draw a
                # line through a counter reset and invent the values under it.
                "custom": {"spanNulls": False, "showPoints": "auto"},
            },
            "overrides": [],
        },
        "targets": [
            {
                "refId": "A",
                "format": "time_series",
                "rawQuery": True,
                "rawSql": panel.sql,
                "datasource": DATASOURCE,
            }
        ],
    }


def build_dashboard() -> dict[str, object]:
    """The dashboard, validated against the live column sets as it is built."""
    panels: list[dict[str, object]] = []
    for index, panel in enumerate(PANELS):
        if panel.missing:
            panels.append(_unmeasured_panel(panel, index))
        else:
            panels.append(_timeseries_panel(panel, index))

    measured = sum(1 for p in PANELS if not p.missing)
    return {
        "title": "VoiceGateway load test",
        "uid": "voicegateway-load-test",
        "schemaVersion": 39,
        "editable": True,
        "time": {"from": "now-6h", "to": "now"},
        "templating": {
            "list": [
                {
                    "name": "DS_VOICEGATEWAY",
                    "type": "datasource",
                    "query": "postgres",
                    "label": "VoiceGateway database",
                }
            ]
        },
        "description": (
            f"{measured} of {len(PANELS)} panels have a series behind them. The "
            "rest are rendered as explicit NOT MEASURED notes rather than empty "
            "graphs, because an empty graph reads as nothing happening when it "
            "means nobody measured. Generated from the node_samples column sets, "
            "so a panel can never name a series that does not exist."
        ),
        "panels": panels,
    }


def dashboard_json() -> str:
    """The dashboard as the checked-in file holds it: stable key order."""
    return json.dumps(build_dashboard(), indent=2, sort_keys=True) + "\n"


__all__ = [
    "DATASOURCE",
    "PANELS",
    "TABLE",
    "Panel",
    "build_dashboard",
    "dashboard_json",
]
