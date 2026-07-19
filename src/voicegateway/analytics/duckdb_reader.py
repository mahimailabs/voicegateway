"""DuckDB read path for local (SQLite) analytics.

Attaches the live SQLite file **read-only** and runs the dashboard's cost and
latency aggregations columnar-fast, returning the SAME dict shapes as
:mod:`voicegateway.repository.cost_repository` and
:mod:`voicegateway.repository.latency_repository`.

It is opt-in (``cost_tracking.read_engine = "duckdb"``) and the service layer
falls back to the SQLite path if DuckDB is unavailable or a query raises, so
this module never changes results, only how fast they come back. Percentiles
use ``quantile_cont`` (linear interpolation), matching
:func:`voicegateway.utils.percentiles.compute_percentiles`. Read-only ATTACH
coexists with the agent's live writes via SQLite's own WAL locking.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from voicegateway.repository.cost_repository import period_since, resolve_window

if TYPE_CHECKING:
    import duckdb

_PCTS: list[float] = [50.0, 95.0, 99.0]


def available() -> bool:
    """Whether the ``duckdb`` package is importable."""
    try:
        import duckdb  # noqa: F401

        return True
    except ImportError:
        return False


@contextlib.contextmanager
def _attached(db_path: Path | str) -> Iterator[duckdb.DuckDBPyConnection]:
    """Yield a DuckDB connection with the SQLite file attached read-only."""
    import duckdb

    con = duckdb.connect()
    try:
        # Core extension; usually autoloads, but be explicit for offline envs.
        with contextlib.suppress(Exception):
            con.execute("INSTALL sqlite")
            con.execute("LOAD sqlite")
        safe = str(db_path).replace("'", "''")
        con.execute(f"ATTACH '{safe}' AS vg (TYPE sqlite, READ_ONLY)")
        yield con
    finally:
        con.close()


def _where(
    *,
    since: float,
    until: float | None,
    project: str | None,
    tenant: str | None,
    agent: str | None,
    include_project: bool = True,
    latency_not_null: bool = False,
) -> tuple[str, list[Any]]:
    """Compose the WHERE clause + positional params, mirroring the ORM repo."""
    params: list[Any] = [since]
    where = "WHERE timestamp >= ?"
    if until is not None:
        where += " AND timestamp < ?"
        params.append(until)
    if include_project and project:
        where += " AND project = ?"
        params.append(project)
    if tenant is not None:
        if tenant == "":
            where += " AND tenant_id IS NULL"
        else:
            where += " AND tenant_id = ?"
            params.append(tenant)
    if agent is not None:
        if agent == "":
            where += " AND agent_id IS NULL"
        else:
            where += " AND agent_id = ?"
            params.append(agent)
    if latency_not_null:
        where += " AND (ttfb_ms IS NOT NULL OR total_latency_ms IS NOT NULL)"
    return where, params


def _pct_key(p: float) -> str:
    """Stable dict key for a percentile, matching utils.percentiles."""
    if p == int(p):
        return f"p{int(p)}"
    return f"p{p}".replace(".", "_")


def _pcts(qs: list[Any] | None, pcts: list[float]) -> dict[str, float | None]:
    """Map a ``quantile_cont`` list (or NULL) to ``{p50: ...}`` keys."""
    if qs is None:
        return {_pct_key(p): None for p in pcts}
    return {
        _pct_key(p): (float(qs[i]) if qs[i] is not None else None)
        for i, p in enumerate(pcts)
    }


def cost_summary(
    db_path: Path | str,
    *,
    period: str = "today",
    project: str | None = None,
    include_pricing_source: bool = False,
    start_ts: float | None = None,
    end_ts: float | None = None,
    tenant: str | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    """DuckDB equivalent of ``cost_repository.get_cost_summary``."""
    since, until = resolve_window(period, start_ts, end_ts)
    where, params = _where(
        since=since, until=until, project=project, tenant=tenant, agent=agent
    )
    with _attached(db_path) as con:
        row = con.execute(
            f"SELECT COALESCE(SUM(cost_usd), 0) FROM vg.requests {where}", params
        ).fetchone()
        total = row[0] if row else 0.0
        by_provider = {
            r[0]: {"cost": r[1], "requests": r[2]}
            for r in con.execute(
                f"SELECT provider, SUM(cost_usd), COUNT(*) FROM vg.requests {where} "
                f"GROUP BY provider ORDER BY 2 DESC",
                params,
            ).fetchall()
        }
        if include_pricing_source:
            by_model = {
                r[0]: {"cost": r[1], "requests": r[2], "pricing_source": r[3] or ""}
                for r in con.execute(
                    f"SELECT model_id, SUM(cost_usd), COUNT(*), "
                    f"string_agg(DISTINCT pricing_source, ',') "
                    f"FROM vg.requests {where} GROUP BY model_id ORDER BY 2 DESC",
                    params,
                ).fetchall()
            }
        else:
            by_model = {
                r[0]: {"cost": r[1], "requests": r[2]}
                for r in con.execute(
                    f"SELECT model_id, SUM(cost_usd), COUNT(*) FROM vg.requests {where} "
                    f"GROUP BY model_id ORDER BY 2 DESC",
                    params,
                ).fetchall()
            }
    return {
        "period": period,
        "project": project,
        "total": total,
        "by_provider": by_provider,
        "by_model": by_model,
    }


def cost_by_day(
    db_path: Path | str,
    *,
    period: str = "week",
    project: str | None = None,
    start_ts: float | None = None,
    end_ts: float | None = None,
    tenant: str | None = None,
    agent: str | None = None,
) -> list[dict[str, Any]]:
    """DuckDB equivalent of ``cost_repository.get_cost_by_day``."""
    since, until = resolve_window(period, start_ts, end_ts)
    where, params = _where(
        since=since, until=until, project=project, tenant=tenant, agent=agent
    )
    with _attached(db_path) as con:
        # FLOOR, not CAST: DuckDB's double->bigint cast rounds, but the SQLite
        # repo truncates (CAST ... AS INTEGER). FLOOR matches for positive epochs.
        rows = con.execute(
            f"SELECT CAST(FLOOR(timestamp / 86400) AS BIGINT) * 86400 AS day, "
            f"SUM(cost_usd), COUNT(*) FROM vg.requests {where} "
            f"GROUP BY day ORDER BY day ASC",
            params,
        ).fetchall()
    return [
        {"day": float(r[0]), "cost": float(r[1] or 0.0), "requests": int(r[2])}
        for r in rows
    ]


def cost_by_project(
    db_path: Path | str,
    *,
    period: str = "today",
    start_ts: float | None = None,
    end_ts: float | None = None,
    tenant: str | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    """DuckDB equivalent of ``cost_repository.get_cost_by_project``."""
    since, until = resolve_window(period, start_ts, end_ts)
    where, params = _where(
        since=since,
        until=until,
        project=None,
        tenant=tenant,
        agent=agent,
        include_project=False,
    )
    with _attached(db_path) as con:
        return {
            r[0]: {"cost": r[1], "requests": r[2]}
            for r in con.execute(
                f"SELECT project, SUM(cost_usd), COUNT(*) FROM vg.requests {where} "
                f"GROUP BY project ORDER BY 2 DESC",
                params,
            ).fetchall()
        }


def cost_by_modality(
    db_path: Path | str,
    *,
    period: str = "today",
    project: str | None = None,
    start_ts: float | None = None,
    end_ts: float | None = None,
) -> dict[str, Any]:
    """DuckDB equivalent of ``cost_repository.get_cost_by_modality``."""
    since, until = resolve_window(period, start_ts, end_ts)
    where, params = _where(
        since=since, until=until, project=project, tenant=None, agent=None
    )
    with _attached(db_path) as con:
        return {
            r[0]: {"cost": r[1], "requests": r[2]}
            for r in con.execute(
                f"SELECT modality, SUM(cost_usd), COUNT(*) FROM vg.requests {where} "
                f"GROUP BY modality ORDER BY 2 DESC",
                params,
            ).fetchall()
        }


def latency_stats(
    db_path: Path | str,
    *,
    period: str = "today",
    project: str | None = None,
    percentiles: list[float] | None = None,
    tenant: str | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    """DuckDB equivalent of ``latency_repository.get_latency_stats``.

    Percentiles are computed in-DB with ``quantile_cont`` (one pass), instead
    of pulling every raw sample into Python.
    """
    pcts = percentiles or _PCTS
    since = period_since(period)
    where, params = _where(
        since=since,
        until=None,
        project=project,
        tenant=tenant,
        agent=agent,
        latency_not_null=True,
    )
    fracs = [p / 100.0 for p in pcts]
    with _attached(db_path) as con:
        rows = con.execute(
            f"SELECT model_id, AVG(ttfb_ms), AVG(total_latency_ms), COUNT(*), "
            f"quantile_cont(ttfb_ms, {fracs}), "
            f"quantile_cont(total_latency_ms, {fracs}) "
            f"FROM vg.requests {where} GROUP BY model_id",
            params,
        ).fetchall()
    return {
        r[0]: {
            "avg_ttfb_ms": r[1],
            "avg_latency_ms": r[2],
            "request_count": r[3],
            "ttfb_percentiles": _pcts(r[4], pcts),
            "latency_percentiles": _pcts(r[5], pcts),
        }
        for r in rows
    }


__all__ = [
    "available",
    "cost_by_day",
    "cost_by_modality",
    "cost_by_project",
    "cost_summary",
    "latency_stats",
]
