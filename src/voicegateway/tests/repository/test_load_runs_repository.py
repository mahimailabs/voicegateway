"""``load_runs`` / ``load_run_tests``: upsert, read, and the honesty rules.

The centrepiece is that NULL survives. Every measured column means "not
measured" when it is NULL, and a repository that helpfully substituted 0 would
turn "the artifact carried no failure breakdown" into "there were no timeouts".
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from voicegateway.models.load_run_model import (  # noqa: F401 - registers tables
    LoadRun,
    LoadRunTest,
)
from voicegateway.repository import load_runs_repository as repo


@pytest.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'runs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(
            SQLModel.metadata.create_all,
            tables=[
                SQLModel.metadata.tables["load_runs"],
                SQLModel.metadata.tables["load_run_tests"],
            ],
        )
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


def _run(**kw) -> repo.LoadRunInput:
    kw.setdefault("id", "run-1")
    kw.setdefault("created_at_ms", 1_785_000_000_000)
    return repo.LoadRunInput(**kw)


def _test(**kw) -> repo.LoadRunTestInput:
    kw.setdefault("run_id", "run-1")
    kw.setdefault("name", "ramp-500")
    kw.setdefault("created_at_ms", 1_785_000_000_000)
    return repo.LoadRunTestInput(**kw)


async def test_a_run_round_trips(db: AsyncSession) -> None:
    written = await repo.upsert_run(db, _run(label="baseline", tool="gossipper"))
    assert written.id == "run-1"
    read = await repo.get_run(db, "run-1")
    assert read is not None
    assert read.label == "baseline"
    assert read.tool == "gossipper"


async def test_an_unknown_run_is_none_not_an_empty_row(db: AsyncSession) -> None:
    assert await repo.get_run(db, "nope") is None


async def test_reimporting_updates_rather_than_duplicating(db: AsyncSession) -> None:
    # An operator who re-runs an import after fixing one field should end up
    # with one run, not two.
    await repo.upsert_run(db, _run(label="first"))
    await repo.upsert_run(db, _run(label="second"))
    runs = await repo.list_runs(db)
    assert len(runs) == 1
    assert runs[0].label == "second"


async def test_reimporting_a_test_updates_on_run_id_and_name(db: AsyncSession) -> None:
    await repo.upsert_run(db, _run())
    await repo.upsert_test(db, _test(peak_concurrency=124))
    await repo.upsert_test(db, _test(peak_concurrency=492))
    tests = await repo.list_tests(db, "run-1")
    assert len(tests) == 1
    assert tests[0].peak_concurrency == 492


async def test_two_tests_with_different_names_coexist(db: AsyncSession) -> None:
    await repo.upsert_run(db, _run())
    await repo.upsert_test(db, _test(name="ramp-100", sequence=0))
    await repo.upsert_test(db, _test(name="ramp-500", sequence=1))
    tests = await repo.list_tests(db, "run-1")
    assert [t.name for t in tests] == ["ramp-100", "ramp-500"]


async def test_tests_are_ordered_by_sequence_not_by_start_time(
    db: AsyncSession,
) -> None:
    # A ramp's steps are only meaningful in order, and a step that never started
    # has no start time to sort by.
    await repo.upsert_run(db, _run())
    await repo.upsert_test(db, _test(name="third", sequence=2, started_at_ms=None))
    await repo.upsert_test(db, _test(name="first", sequence=0, started_at_ms=999))
    await repo.upsert_test(db, _test(name="second", sequence=1, started_at_ms=None))
    assert [t.name for t in await repo.list_tests(db, "run-1")] == [
        "first",
        "second",
        "third",
    ]


async def test_unmeasured_columns_stay_null(db: AsyncSession) -> None:
    """The whole point. A repository that substituted 0 would invent readings."""
    await repo.upsert_run(db, _run())
    await repo.upsert_test(db, _test())
    [t] = await repo.list_tests(db, "run-1")
    for name in (
        "peak_concurrency",
        "attempted_calls",
        "succeeded_calls",
        "failed_calls",
        "failed_timeout",
        "peak_cpu_utilisation",
        "peak_memory_utilisation",
        "rtp_packets_sent",
        "rtp_packets_received",
    ):
        assert getattr(t, name) is None, f"{name} was defaulted rather than left NULL"


async def test_zero_is_kept_distinct_from_not_measured(db: AsyncSession) -> None:
    # A test that genuinely had no timeouts stores 0; one whose breakdown was
    # never parsed stores NULL. Both must survive the round trip.
    await repo.upsert_run(db, _run())
    await repo.upsert_test(db, _test(name="a", failed_timeout=0))
    await repo.upsert_test(db, _test(name="b"))
    by_name = {t.name: t for t in await repo.list_tests(db, "run-1")}
    assert by_name["a"].failed_timeout == 0
    assert by_name["b"].failed_timeout is None


async def test_failures_by_cause_omits_what_was_not_parsed(db: AsyncSession) -> None:
    await repo.upsert_run(db, _run())
    await repo.upsert_test(db, _test(failed_timeout=3, failed_unexpected_sip=9))
    [t] = await repo.list_tests(db, "run-1")
    causes = t.failures_by_cause
    assert causes == {"timeout": 3, "unexpected_sip": 9}
    # The unparsed causes are absent, not zero: a 0 would claim there were none.
    assert "parse_error" not in causes


async def test_duration_is_none_when_either_end_is_missing(db: AsyncSession) -> None:
    await repo.upsert_run(db, _run())
    await repo.upsert_test(db, _test(name="open", started_at_ms=1000))
    await repo.upsert_test(
        db, _test(name="closed", started_at_ms=1000, ended_at_ms=4000)
    )
    by_name = {t.name: t for t in await repo.list_tests(db, "run-1")}
    # A test whose end was never written did not last no time.
    assert by_name["open"].duration_ms is None
    assert by_name["closed"].duration_ms == 3000


async def test_provenance_comes_from_the_checksum_not_a_flag(db: AsyncSession) -> None:
    # A report calls itself measured only when a real artifact was hashed, so
    # nothing can assert measured-ness without holding the artifact.
    synthetic = await repo.upsert_run(db, _run(id="fixtures"))
    assert synthetic.has_artifact is False
    measured = await repo.upsert_run(db, _run(id="real", artifact_sha256="a" * 64))
    assert measured.has_artifact is True


async def test_runs_list_newest_first_and_can_be_scoped_to_a_project(
    db: AsyncSession,
) -> None:
    await repo.upsert_run(db, _run(id="old", created_at_ms=1000, project="p1"))
    await repo.upsert_run(db, _run(id="new", created_at_ms=9000, project="p1"))
    await repo.upsert_run(db, _run(id="other", created_at_ms=5000, project="p2"))
    assert [r.id for r in await repo.list_runs(db)] == ["new", "other", "old"]
    assert [r.id for r in await repo.list_runs(db, project="p1")] == ["new", "old"]
    # None means every project, matching the sibling repositories.
    assert len(await repo.list_runs(db, project=None)) == 3


async def test_the_list_limit_is_honoured(db: AsyncSession) -> None:
    for i in range(5):
        await repo.upsert_run(db, _run(id=f"r{i}", created_at_ms=1000 + i))
    assert len(await repo.list_runs(db, limit=2)) == 2


async def test_tests_of_an_unknown_run_are_empty_not_an_error(
    db: AsyncSession,
) -> None:
    assert await repo.list_tests(db, "nope") == []


async def test_the_rows_are_json_safe(db: AsyncSession) -> None:
    import json

    await repo.upsert_run(db, _run(artifact_sha256="b" * 64))
    await repo.upsert_test(db, _test(peak_cpu_utilisation=0.63))
    run = await repo.get_run(db, "run-1")
    assert run is not None
    json.loads(json.dumps(run.as_dict()))
    for t in await repo.list_tests(db, "run-1"):
        json.loads(json.dumps(t.as_dict()))
