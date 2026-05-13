"""Async repo for the ``latency_observations`` table.

Implements REQ-VG-ROUTE-002 AC-4 (fallback estimate path) and the
read side of REQ-VG-ROUTE-003 (per-project provider-latency
snapshot the dashboard Routing view displays).

The table is a denormalized rollup: one row per
(project_id, provider, modality) holding the latest p50/p95 over a
configurable trailing window. The
``latency_observations_worker`` (T06) calls ``roll_up`` every
fifteen minutes; the router (T05) and the dashboard's
``/api/routing/observations`` endpoint (T12) both call
``read_all`` / ``get_for_project`` to consume the snapshot.

Aggregation source: the v0.0.5 ``requests`` table. Each request row
carries ``project``, ``provider``, ``modality``, ``total_latency_ms``,
``timestamp``. Aggregating per (project, provider, modality) over a
trailing window gives the router the predicted per-modality latency
it needs.

The Foundry's "use ``turns`` when v0.2.0 shipped" design was
revisited: the ``turns`` table records full turn-cycle latency
without per-modality breakdown, so it cannot answer the router's
"how fast is provider X at modality Y" question. ``requests`` carries
the per-modality timing directly and works uniformly on v0.0.5,
v0.2.0+, and v0.4.0+ baselines.

Schema reference:
``voicegateway/storage/migrations/0006_routing_and_branding.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from voicegateway.storage._percentiles import compute_percentiles

if TYPE_CHECKING:
    import aiosqlite


_DEFAULT_WINDOW_MINUTES = 24 * 60  # OQ2 lock: 24h rolling window.


@dataclass(frozen=True)
class LatencyObservation:
    """One row from the ``latency_observations`` rollup."""

    project_id: str
    provider: str
    modality: str
    p50_ms: int | None
    p95_ms: int | None
    sample_count: int
    window_start: str
    window_end: str
    refreshed_at: str


def _row_to_observation(row: Sequence[Any]) -> LatencyObservation:
    return LatencyObservation(
        project_id=str(row[0]),
        provider=str(row[1]),
        modality=str(row[2]),
        p50_ms=None if row[3] is None else int(row[3]),
        p95_ms=None if row[4] is None else int(row[4]),
        sample_count=int(row[5]),
        window_start=str(row[6]),
        window_end=str(row[7]),
        refreshed_at=str(row[8]),
    )


_SELECT_FIELDS = (
    "project_id, provider, modality, p50_ms, p95_ms, "
    "sample_count, window_start, window_end, refreshed_at"
)


async def roll_up(
    db: aiosqlite.Connection, *, window_minutes: int = _DEFAULT_WINDOW_MINUTES
) -> int:
    """Recompute the rollup over the trailing window.

    Deletes the previous rollup snapshot atomically and re-inserts
    one row per (project, provider, modality) with new p50/p95 over
    the requests table window. Returns the number of inserted rows.

    The "delete + insert" pattern keeps the table size bounded
    (one row per group) and ensures the FE never sees a half-rolled
    state: a single transaction commits the new snapshot.

    ``window_minutes`` defaults to the OQ2-locked 1440 minutes
    (24 hours). Past requests outside the window are ignored.
    """
    import datetime as _dt

    now = _dt.datetime.now(tz=_dt.UTC)
    window_start = now - _dt.timedelta(minutes=window_minutes)
    window_start_ts = window_start.timestamp()
    window_end_ts = now.timestamp()
    window_start_iso = window_start.isoformat()
    window_end_iso = now.isoformat()

    cursor = await db.execute(
        """SELECT project, provider, modality, total_latency_ms
           FROM requests
           WHERE timestamp >= ?
             AND timestamp < ?
             AND total_latency_ms IS NOT NULL""",
        (window_start_ts, window_end_ts),
    )
    rows = await cursor.fetchall()

    by_group: dict[tuple[str, str, str], list[float]] = {}
    for row in rows:
        project, provider, modality, latency = row
        if latency is None:
            continue
        key = (str(project), str(provider), str(modality))
        by_group.setdefault(key, []).append(float(latency))

    await db.execute("DELETE FROM latency_observations")

    inserted = 0
    for (project, provider, modality), samples in by_group.items():
        pcts = compute_percentiles(samples, [50.0, 95.0])
        p50 = pcts.get("p50")
        p95 = pcts.get("p95")
        await db.execute(
            "INSERT INTO latency_observations "
            "(project_id, provider, modality, p50_ms, p95_ms, "
            " sample_count, window_start, window_end) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project,
                provider,
                modality,
                None if p50 is None else int(p50),
                None if p95 is None else int(p95),
                len(samples),
                window_start_iso,
                window_end_iso,
            ),
        )
        inserted += 1

    await db.commit()
    return inserted


async def read_all(db: aiosqlite.Connection) -> list[LatencyObservation]:
    """Return every row in the current rollup snapshot.

    Ordered by ``project_id ASC, modality ASC, provider ASC`` for
    deterministic dashboard display.
    """
    cursor = await db.execute(
        f"SELECT {_SELECT_FIELDS} FROM latency_observations "
        "ORDER BY project_id ASC, modality ASC, provider ASC"
    )
    return [_row_to_observation(row) async for row in cursor]


async def get_for_project(
    db: aiosqlite.Connection, project_id: str
) -> list[LatencyObservation]:
    """Return the rollup rows for one project. Used by the router."""
    cursor = await db.execute(
        f"SELECT {_SELECT_FIELDS} FROM latency_observations "
        "WHERE project_id = ? "
        "ORDER BY modality ASC, provider ASC",
        (project_id,),
    )
    return [_row_to_observation(row) async for row in cursor]


async def get_one(
    db: aiosqlite.Connection,
    *,
    project_id: str,
    provider: str,
    modality: str,
) -> LatencyObservation | None:
    """Return a single (project, provider, modality) observation or None."""
    cursor = await db.execute(
        f"SELECT {_SELECT_FIELDS} FROM latency_observations "
        "WHERE project_id = ? AND provider = ? AND modality = ?",
        (project_id, provider, modality),
    )
    row = await cursor.fetchone()
    return _row_to_observation(row) if row is not None else None


__all__ = [
    "LatencyObservation",
    "get_for_project",
    "get_one",
    "read_all",
    "roll_up",
]
