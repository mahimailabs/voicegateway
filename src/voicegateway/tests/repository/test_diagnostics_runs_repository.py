"""The diagnostics-run store: full-overwrite upsert, ordering, JSON tolerance.

A run is written three times (queued -> running -> terminal) by one owning
process, so the upsert overwrites the whole record rather than merging fields --
the opposite of ``calls_repository``, whose writers each hold a subset. These
tests pin that, the newest-first ordering the history endpoint relies on, and
that opaque probe payloads come back exactly as they went in.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from voicegateway.models.diagnostics_run_model import (  # noqa: F401 - registers table
    DiagnosticsRun,
)
from voicegateway.repository import diagnostics_runs_repository as repo


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    session = AsyncSession(engine)
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


async def test_upsert_then_read_round_trips(db: AsyncSession) -> None:
    results = {"verdict": "PASS", "checks": {"agents": {"ok": True, "agents": []}}}
    await repo.upsert_run(
        db,
        run_id="r1",
        checks=["agents", "latency"],
        config={"participants": 4},
        status="done",
        results=results,
        verdict="PASS",
        created_at="2026-07-30T01:00:00+00:00",
        started_at="2026-07-30T01:00:01+00:00",
        ended_at="2026-07-30T01:00:09+00:00",
    )

    row = await repo.get_run(db, "r1")
    assert row is not None
    assert row.run_id == "r1"
    assert row.checks == ["agents", "latency"]
    assert row.config == {"participants": 4}
    assert row.results == results
    assert row.verdict == "PASS"
    assert row.error is None
    assert row.status == "done"
    assert row.created_at == "2026-07-30T01:00:00+00:00"
    assert row.started_at == "2026-07-30T01:00:01+00:00"
    assert row.ended_at == "2026-07-30T01:00:09+00:00"
    assert row.project == repo.DEFAULT_PROJECT


async def test_get_run_returns_none_for_unknown_id(db: AsyncSession) -> None:
    assert await repo.get_run(db, "nope") is None


async def test_state_transitions_overwrite_the_same_row(db: AsyncSession) -> None:
    """queued -> running -> failed is three writes to one row, not three rows."""
    created = "2026-07-30T02:00:00+00:00"
    await repo.upsert_run(
        db, run_id="r1", checks=["sfu"], config={}, status="queued", created_at=created
    )
    await repo.upsert_run(
        db,
        run_id="r1",
        checks=["sfu"],
        config={},
        status="running",
        created_at=created,
        started_at="2026-07-30T02:00:01+00:00",
    )
    await repo.upsert_run(
        db,
        run_id="r1",
        checks=["sfu"],
        config={},
        status="failed",
        error="run timed out",
        created_at=created,
        started_at="2026-07-30T02:00:01+00:00",
        ended_at="2026-07-30T02:06:01+00:00",
    )

    rows = await repo.list_runs(db)
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert rows[0].error == "run timed out"
    assert rows[0].started_at == "2026-07-30T02:00:01+00:00"


async def test_no_results_stays_null_and_is_not_an_empty_dict(
    db: AsyncSession,
) -> None:
    """"No results were recorded" is a different claim from "{}"."""
    await repo.upsert_run(
        db,
        run_id="r1",
        checks=["sfu"],
        config={},
        status="failed",
        error="boom",
        created_at="2026-07-30T03:00:00+00:00",
    )
    row = await repo.get_run(db, "r1")
    assert row is not None
    assert row.results is None

    stored = await db.execute(
        text("SELECT results_json FROM diagnostics_runs WHERE run_id = 'r1'")
    )
    assert stored.scalar() is None

    await repo.upsert_run(
        db,
        run_id="r2",
        checks=["sfu"],
        config={},
        status="done",
        results={},
        created_at="2026-07-30T03:00:00+00:00",
    )
    empty = await repo.get_run(db, "r2")
    assert empty is not None
    assert empty.results == {}


async def test_probe_payloads_are_stored_unchanged(db: AsyncSession) -> None:
    """This layer stores what the probe returned and derives nothing.

    Notably a ``None`` knee (which means two opposite things upstream) and a
    hardcoded ``loss_pct`` survive verbatim: normalising either here would
    invent a measurement.
    """
    results = {
        "verdict": "WARN",
        "checks": {
            "sfu_load": {
                "ok": True,
                "baseline": {"rtt_ms": 5.0, "loss_pct": 0.0, "quality": "Excellent"},
                "knee": None,
                "ramp": [{"participants": 2, "rtt_ms": 6.5}],
            }
        },
    }
    await repo.upsert_run(
        db,
        run_id="r1",
        checks=["sfu_load"],
        config={"max_participants": 8},
        status="done",
        results=results,
        verdict="WARN",
        created_at="2026-07-30T04:00:00+00:00",
    )
    row = await repo.get_run(db, "r1")
    assert row is not None
    assert row.results == results


async def test_list_runs_is_newest_first_and_bounded(db: AsyncSession) -> None:
    for i in range(5):
        await repo.upsert_run(
            db,
            run_id=f"r{i}",
            checks=["agents"],
            config={},
            status="done",
            created_at=f"2026-07-30T05:0{i}:00+00:00",
        )

    rows = await repo.list_runs(db)
    assert [r.run_id for r in rows] == ["r4", "r3", "r2", "r1", "r0"]

    assert [r.run_id for r in await repo.list_runs(db, limit=2)] == ["r4", "r3"]


async def test_runs_sharing_a_timestamp_get_a_stable_tiebreak(
    db: AsyncSession,
) -> None:
    same = "2026-07-30T06:00:00+00:00"
    for run_id in ("aaa", "bbb", "ccc"):
        await repo.upsert_run(
            db,
            run_id=run_id,
            checks=["agents"],
            config={},
            status="done",
            created_at=same,
        )
    assert [r.run_id for r in await repo.list_runs(db)] == ["ccc", "bbb", "aaa"]


async def test_unparseable_json_column_degrades_loudly_not_fatally(
    db: AsyncSession,
) -> None:
    """A truncated write must not take the whole history read down with it."""
    await db.execute(
        text(
            "INSERT INTO diagnostics_runs "
            "(run_id, project, status, checks_json, config_json, results_json, "
            " created_at) "
            "VALUES ('bad', 'default', 'done', 'not json{', '{}', 'also bad{', "
            "'2026-07-30T07:00:00+00:00')"
        )
    )
    await db.commit()

    row = await repo.get_run(db, "bad")
    assert row is not None
    assert row.checks == []
    assert row.results is None
    assert row.status == "done"


async def test_wrong_json_type_is_coerced_to_the_right_empty_value(
    db: AsyncSession,
) -> None:
    """A caller reading ``checks`` must never have to type-check it."""
    await db.execute(
        text(
            "INSERT INTO diagnostics_runs "
            "(run_id, project, status, checks_json, config_json, results_json, "
            " created_at) "
            "VALUES ('odd', 'default', 'done', '\"agents\"', '[]', '7', "
            "'2026-07-30T08:00:00+00:00')"
        )
    )
    await db.commit()

    row = await repo.get_run(db, "odd")
    assert row is not None
    assert row.checks == []
    assert row.config == {}
    assert row.results is None


async def test_project_is_stored_for_retention(db: AsyncSession) -> None:
    await repo.upsert_run(
        db,
        run_id="r1",
        checks=["agents"],
        config={},
        status="done",
        created_at="2026-07-30T09:00:00+00:00",
        project="acme",
    )
    row = await repo.get_run(db, "r1")
    assert row is not None
    assert row.project == "acme"
