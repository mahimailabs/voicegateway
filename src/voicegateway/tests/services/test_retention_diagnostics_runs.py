"""Diagnostics runs must age out. Persistence without retention is a leak.

A run has no children, so nothing is deleted before it, but it does carry a full
probe payload -- an unbounded table of those is the cost of taking the history off
the process-local 20-entry cap. It ages on ISO-8601 strings compared lexically,
the same way the session pass ages ``sessions.ended_at``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from voicegateway.services.retention_service import RetentionWorker
from voicegateway.services.storage_service import StorageService


@pytest.fixture
async def storage(tmp_path):
    s = StorageService(str(tmp_path / "retention-diag.db"))
    await s._ensure_initialized()
    return s


def _provider(project: str, days: int):
    async def provider():
        return [(project, days)]

    return provider


def _iso_days_ago(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


async def _count(storage, where: str, params: dict) -> int:
    async with storage._conn.session() as db:
        result = await db.execute(
            text(f"SELECT COUNT(*) FROM diagnostics_runs WHERE {where}"), params
        )
        return int(result.scalar() or 0)


async def _run(storage, run_id: str, **kwargs) -> str:
    kwargs.setdefault("checks", ["agents"])
    kwargs.setdefault("config", {})
    kwargs.setdefault("status", "done")
    await storage.upsert_diagnostics_run(run_id=run_id, **kwargs)
    return run_id


async def test_aged_run_is_deleted_and_a_young_one_is_kept(storage) -> None:
    await _run(
        storage,
        "old",
        project="acme",
        created_at=_iso_days_ago(11),
        started_at=_iso_days_ago(11),
        ended_at=_iso_days_ago(10),
    )
    await _run(
        storage,
        "young",
        project="acme",
        created_at=_iso_days_ago(1),
        ended_at=_iso_days_ago(1),
    )

    await RetentionWorker(storage, retention_provider=_provider("acme", 5)).tick_now()

    assert await _count(storage, "run_id = :i", {"i": "old"}) == 0
    assert await _count(storage, "run_id = :i", {"i": "young"}) == 1


async def test_a_run_that_never_ended_still_ages_out(storage) -> None:
    """A process killed mid-run leaves a row at 'running' forever otherwise."""
    await _run(
        storage,
        "stuck",
        project="acme",
        status="running",
        created_at=_iso_days_ago(30),
        started_at=_iso_days_ago(30),
    )
    await RetentionWorker(storage, retention_provider=_provider("acme", 5)).tick_now()
    assert await _count(storage, "run_id = :i", {"i": "stuck"}) == 0


async def test_another_projects_runs_are_untouched(storage) -> None:
    await _run(storage, "mine", project="acme", created_at=_iso_days_ago(10))
    await _run(storage, "theirs", project="other", created_at=_iso_days_ago(10))
    await RetentionWorker(storage, retention_provider=_provider("acme", 5)).tick_now()
    assert await _count(storage, "run_id = :i", {"i": "mine"}) == 0
    assert await _count(storage, "run_id = :i", {"i": "theirs"}) == 1


async def test_prune_is_idempotent_and_batched(storage) -> None:
    for i in range(120):
        await _run(
            storage, f"r{i:03d}", project="acme", created_at=_iso_days_ago(10 + i / 100)
        )
    worker = RetentionWorker(
        storage, retention_provider=_provider("acme", 5), batch_size=50
    )
    first = await worker.tick_now()
    second = await worker.tick_now()
    assert first["acme"] == 120
    assert second["acme"] == 0
    assert await _count(storage, "project = :p", {"p": "acme"}) == 0
