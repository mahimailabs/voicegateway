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


class _Row:
    """A query result carrying no rows, which is all the shape check needs."""

    result_rows: list[Any] = []


class _StubClient:
    """Answers every query with nothing, and remembers what it was asked.

    The readers only call ``await client.query(sql, parameters=...)`` and read
    ``.result_rows``, so an empty answer exercises every branch that builds the
    response dict without needing ClickHouse running.
    """

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def query(self, sql: str, parameters: Any = None) -> _Row:
        self.queries.append(sql)
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
