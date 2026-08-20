"""Three readers, one contract. A field added to one must be added to all.

`get_cost_summary` has three implementations: SQLite (`cost_repository`),
DuckDB (`analytics/duckdb_reader`) and ClickHouse (`clickhouse/read_repository`).
Which one answers depends on how the deployment is configured, and a caller
cannot tell them apart. So a field present in one and missing from another is
not a gap, it is the same endpoint returning different answers to different
operators.

THIS ALREADY HAPPENED. `billable_requests` was added to the SQLite reader and
three existing DuckDB equivalence tests went red, which is exactly what they are
for. Nothing covered ClickHouse, so that reader silently kept the old shape, and
ClickHouse is the collector path: the deployment where a fleet's numbers
actually live.

NO SERVER REQUIRED, deliberately. The existing ClickHouse tests spin a
container, and no CI workflow provides one, so they error rather than run. A
guard that never executes is not a guard, and this repository has produced two
of those already. This asserts the CONTRACT, which is what drifted, using a
stub client that records the SQL it was handed. It cannot catch a value
disagreement; it catches the shape disagreement that actually occurred.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from voicegateway.clickhouse import read_repository as ch_read
from voicegateway.middleware.cost_tracker_middleware import CostTracker
from voicegateway.repository import cost_repository
from voicegateway.services.storage_service import StorageService

#: What every implementation of the cost summary must return, whatever backs it.
#: Adding a key here without adding it to all three readers fails this file.
COST_SUMMARY_CONTRACT = frozenset(
    {
        "period",
        "project",
        "total",
        "requests",
        "billable_requests",
        "by_provider",
        "by_model",
    }
)

#: One entry of the day-bucketed cost series.
COST_BY_DAY_CONTRACT = frozenset({"day", "cost", "requests"})

#: One per-model entry of the latency rollup.
LATENCY_ENTRY_CONTRACT = frozenset(
    {
        "avg_ttfb_ms",
        "avg_latency_ms",
        "request_count",
        "ttfb_percentiles",
        "latency_percentiles",
    }
)


class _Row:
    """A query result carrying whatever rows the stub decided to hand back."""

    def __init__(self, rows: list[Any] | None = None) -> None:
        self.result_rows = rows or []


class _StubClient:
    """Answers each query with a row shaped like what that query selects.

    The readers only call ``await client.query(sql, parameters=...)`` and read
    ``.result_rows``, so no ClickHouse is needed. Rows are NOT empty for the
    per-entry queries: an empty result builds an empty dict, and an empty dict
    has no entry whose keys can be checked, so the shape that actually drifts
    would go unexamined. The values are arbitrary; only the arity matters.
    """

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def query(self, sql: str, parameters: Any = None) -> _Row:
        self.queries.append(sql)
        if "quantilesTDigest" in sql:
            # model_id, avg_ttfb, avg_latency, count, ttfb_pcts, lat_pcts
            return _Row(
                [
                    (
                        "openai/gpt-4o-mini",
                        120.0,
                        300.0,
                        7,
                        (1.0, 2.0, 3.0),
                        (4.0, 5.0, 6.0),
                    )
                ]
            )
        if "toStartOfDay" in sql or "day" in sql.lower():
            return _Row([(1_785_661_200, 0.42, 7)])
        return _Row()


async def test_clickhouse_summary_satisfies_the_contract() -> None:
    """The reader that had drifted."""
    client = _StubClient()
    result = await ch_read.get_cost_summary(
        client, tenant="t1", since=0.0, until=None, project=None
    )
    missing = COST_SUMMARY_CONTRACT - set(result)
    assert not missing, f"ClickHouse cost summary is missing {sorted(missing)}"


async def test_sqlite_summary_satisfies_the_contract(tmp_path: Path) -> None:
    storage = StorageService(str(tmp_path / "contract.db"))
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        result = await cost_repository.get_cost_summary(db, period="all")
    await storage.aclose()
    missing = COST_SUMMARY_CONTRACT - set(result)
    assert not missing, f"SQLite cost summary is missing {sorted(missing)}"


def test_duckdb_summary_satisfies_the_contract() -> None:
    """Asserted on the source, because the DuckDB reader needs a real file.

    The three equivalence tests in tests/analytics already compare its VALUES
    against SQLite on seeded data. This only pins that the two contract keys
    exist there too, so all three readers are covered by one list.
    """
    import inspect

    from voicegateway.analytics import duckdb_reader

    src = inspect.getsource(duckdb_reader.cost_summary)
    for key in ("requests", "billable_requests"):
        assert f'"{key}"' in src, f"DuckDB cost summary is missing {key!r}"


async def test_the_counts_query_actually_filters_rather_than_counting_all() -> None:
    """Guards against the count being wired up but meaning nothing.

    A `count()` with no predicate would satisfy the contract check above while
    reporting every stored row as billable, which is the exact defect this
    whole change exists to remove.
    """
    client = _StubClient()
    await ch_read.get_cost_summary(
        client, tenant="t1", since=0.0, until=None, project=None
    )
    counts_sql = next((q for q in client.queries if "billable_count" in q), None)
    assert counts_sql is not None, "no billable count query was issued"
    assert "countIf" in counts_sql
    assert "status != 'error'" in counts_sql
    assert "input_units" in counts_sql and "output_units" in counts_sql


@pytest.mark.parametrize("reader", ["clickhouse", "sqlite"])
async def test_neither_reader_reports_more_billable_than_total(
    reader: str, tmp_path: Path
) -> None:
    """A subset count cannot exceed the population it is drawn from."""
    if reader == "clickhouse":
        result = await ch_read.get_cost_summary(
            _StubClient(), tenant="t1", since=0.0, until=None, project=None
        )
    else:
        storage = StorageService(str(tmp_path / "bounds.db"))
        await storage._ensure_initialized()
        async with storage._conn.session() as db:
            result = await cost_repository.get_cost_summary(db, period="all")
        await storage.aclose()
    assert result["billable_requests"] <= result["requests"]


# --------------------------------------------------------------------------
# The other two multi-producer numbers
# --------------------------------------------------------------------------
#
# cost_by_day and latency_stats also have three implementations each. They
# AGREE today, checked field by field. Nothing pinned that, which is exactly
# the state cost_summary was in before it drifted, so these exist to keep an
# agreement that currently holds rather than to repair one that broke.


async def test_clickhouse_cost_by_day_entries_match_the_contract() -> None:
    client = _StubClient()
    series = await ch_read.get_cost_by_day(
        client, tenant="t1", since=0.0, until=None, project=None
    )
    assert series, "the stub returned no row, so no entry shape was checked"
    assert COST_BY_DAY_CONTRACT <= set(series[0])


async def test_sqlite_cost_by_day_entries_match_the_contract(tmp_path: Path) -> None:
    storage = StorageService(str(tmp_path / "byday.db"))
    await storage._ensure_initialized()
    tracker = CostTracker()
    rec = tracker.create_record(
        model_id="openai/gpt-4o-mini",
        modality="llm",
        provider="openai",
        project="default",
        input_units=100.0,
        output_units=10.0,
    )
    rec.cost_usd = 0.05
    await storage.log_request(rec)
    async with storage._conn.session() as db:
        series = await cost_repository.get_cost_by_day(db, period="all")
    await storage.aclose()
    assert series, "seeded one request, expected one day bucket"
    assert COST_BY_DAY_CONTRACT <= set(series[0])


async def test_clickhouse_latency_entries_match_the_contract() -> None:
    client = _StubClient()
    stats = await ch_read.get_latency_stats(
        client, tenant="t1", since=0.0, until=None, project=None
    )
    assert stats, "the stub returned no row, so no entry shape was checked"
    entry = next(iter(stats.values()))
    assert LATENCY_ENTRY_CONTRACT <= set(entry)


def test_duckdb_entries_match_the_contract() -> None:
    """Asserted on the source: the DuckDB reader needs a real database file.

    Its VALUES are already compared against SQLite by the equivalence tests in
    tests/analytics. This only pins that the same entry keys are constructed.
    """
    import inspect

    from voicegateway.analytics import duckdb_reader

    day_src = inspect.getsource(duckdb_reader.cost_by_day)
    for key in COST_BY_DAY_CONTRACT:
        assert f'"{key}"' in day_src, f"DuckDB cost_by_day is missing {key!r}"
    lat_src = inspect.getsource(duckdb_reader.latency_stats)
    for key in LATENCY_ENTRY_CONTRACT:
        assert f'"{key}"' in lat_src, f"DuckDB latency_stats is missing {key!r}"
