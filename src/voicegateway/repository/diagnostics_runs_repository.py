"""Async repo for the ``diagnostics_runs`` table.

The dashboard writes one row per LiveKit diagnostics run and reads the history
back, so a run survives the process that started it. Three properties this
module exists to hold:

* **Full overwrite, not a merge.** One process owns one run start to finish and
  always has the whole record in hand, so :func:`upsert_run` writes every column.
  This is the opposite of ``calls_repository``, where independent out-of-order
  writers each carry a subset of the truth and ``None`` has to mean "this event
  did not say".
* **Select-then-update, never a native ON CONFLICT.** ``run_id`` is a NOT NULL
  primary key, so an on-conflict upsert would in fact be safe here -- but every
  other repository in this package upserts this way (see
  ``workers_repository.upsert_heartbeat`` for the NULL-key reason it started),
  and one shape that behaves identically on SQLite and PostgreSQL is worth more
  than one saved round-trip on a table written three times per run.
* **Stored payloads are returned unchanged.** ``checks``, ``config`` and
  ``results`` are opaque probe output. Nothing here normalises a probe result,
  fills in a missing measurement, or computes a percentile.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from voicegateway.models.diagnostics_run_model import DiagnosticsRun

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_logger = logging.getLogger(__name__)

DEFAULT_PROJECT = "default"

# How many runs a history read returns at most. The stored history is NOT capped
# (retention ages it out); this bounds one response, because a run record carries
# its full probe payload and the dashboard renders every row it is given.
DEFAULT_HISTORY_LIMIT = 100


@dataclass(frozen=True)
class DiagnosticsRunRow:
    """One ``diagnostics_runs`` row as served to readers, JSON already parsed.

    ``project`` is carried for completeness; it is a retention attribute and the
    diagnostics endpoints do not put it on the wire.
    """

    run_id: str
    project: str
    status: str
    checks: list[str]
    config: dict[str, Any]
    results: dict[str, Any] | None
    verdict: str | None
    error: str | None
    created_at: str
    started_at: str | None
    ended_at: str | None


def _loads(raw: str | None, fallback: Any, *, run_id: str, column: str) -> Any:
    """Parse a stored JSON column, falling back loudly rather than raising.

    Every writer here goes through ``json.dumps``, so an unparseable column means
    a truncated write or a hand-edited database. One such row must not take the
    whole history down with it, and it must not pass silently either.
    """
    if raw is None:
        return fallback
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        _logger.warning(
            "diagnostics_runs.%s for run %s is not valid JSON; serving %r",
            column,
            run_id,
            fallback,
        )
        return fallback


def _row(run: DiagnosticsRun) -> DiagnosticsRunRow:
    checks = _loads(run.checks_json, [], run_id=run.run_id, column="checks_json")
    config = _loads(run.config_json, {}, run_id=run.run_id, column="config_json")
    results = _loads(run.results_json, None, run_id=run.run_id, column="results_json")
    return DiagnosticsRunRow(
        run_id=run.run_id,
        project=run.project,
        status=run.status,
        # A payload that parsed to the wrong JSON type is coerced to the empty
        # value of the right one, so a caller never has to type-check it.
        checks=checks if isinstance(checks, list) else [],
        config=config if isinstance(config, dict) else {},
        results=results if isinstance(results, dict) else None,
        verdict=run.verdict,
        error=run.error,
        created_at=run.created_at,
        started_at=run.started_at,
        ended_at=run.ended_at,
    )


def _assign(
    run: DiagnosticsRun,
    *,
    project: str,
    status: str,
    checks: list[str],
    config: dict[str, Any],
    results: dict[str, Any] | None,
    verdict: str | None,
    error: str | None,
    created_at: str,
    started_at: str | None,
    ended_at: str | None,
) -> None:
    """Write the whole record onto ``run`` (see the module docstring: no merge)."""
    run.project = project
    run.status = status
    run.checks_json = json.dumps(checks)
    run.config_json = json.dumps(config)
    # None stays NULL: "no results recorded" is not "{}".
    run.results_json = None if results is None else json.dumps(results)
    run.verdict = verdict
    run.error = error
    run.created_at = created_at
    run.started_at = started_at
    run.ended_at = ended_at


async def upsert_run(
    db: AsyncSession,
    *,
    run_id: str,
    checks: list[str],
    config: dict[str, Any],
    status: str,
    results: dict[str, Any] | None = None,
    verdict: str | None = None,
    error: str | None = None,
    created_at: str,
    started_at: str | None = None,
    ended_at: str | None = None,
    project: str = DEFAULT_PROJECT,
) -> None:
    """Store one run, creating the row on first write and overwriting it after.

    Called once per state transition (queued -> running -> terminal), so a run
    interrupted by a restart leaves behind its last observed state rather than
    nothing at all.
    """
    stmt = select(DiagnosticsRun).where(
        DiagnosticsRun.run_id == run_id  # type: ignore[arg-type]
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        row = DiagnosticsRun(run_id=run_id, checks_json="[]", config_json="{}")
        db.add(row)
    _assign(
        row,
        project=project,
        status=status,
        checks=checks,
        config=config,
        results=results,
        verdict=verdict,
        error=error,
        created_at=created_at,
        started_at=started_at,
        ended_at=ended_at,
    )
    try:
        await db.commit()
    except IntegrityError:
        # A concurrent first insert of the same run_id. Re-resolve and overwrite:
        # this process owns the run, so its record is the authoritative one.
        await db.rollback()
        row = (await db.execute(stmt)).scalar_one()
        _assign(
            row,
            project=project,
            status=status,
            checks=checks,
            config=config,
            results=results,
            verdict=verdict,
            error=error,
            created_at=created_at,
            started_at=started_at,
            ended_at=ended_at,
        )
        await db.commit()


async def get_run(db: AsyncSession, run_id: str) -> DiagnosticsRunRow | None:
    """One run by id, or None."""
    stmt = select(DiagnosticsRun).where(
        DiagnosticsRun.run_id == run_id  # type: ignore[arg-type]
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    return None if row is None else _row(row)


async def list_runs(
    db: AsyncSession, *, limit: int = DEFAULT_HISTORY_LIMIT
) -> list[DiagnosticsRunRow]:
    """Newest runs first, bounded by ``limit``.

    Ordering is by the stored ISO-8601 ``created_at`` string, which sorts
    correctly because every writer formats it the same way
    (``datetime.now(UTC).isoformat()``, always ``+00:00``). ``run_id`` is the
    stable tiebreak for two runs that share a timestamp.
    """
    stmt = (
        select(DiagnosticsRun)
        .order_by(
            DiagnosticsRun.created_at.desc(),  # type: ignore[attr-defined]
            DiagnosticsRun.run_id.desc(),  # type: ignore[attr-defined]
        )
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [_row(r) for r in rows]


__all__ = [
    "DEFAULT_HISTORY_LIMIT",
    "DEFAULT_PROJECT",
    "DiagnosticsRunRow",
    "get_run",
    "list_runs",
    "upsert_run",
]
