"""Node samples must also age out on the operator's configured clock.

The scrape worker trims the table itself on every tick -- that is what actually
bounds ~57k rows/day, because this pass is opt-in and only ever sees projects
discovered from ``requests``/``sessions``. This file pins the second line of
defence: when retention IS configured, samples go with everything else, and one
project's samples never take another's with them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from voicegateway.repository import node_samples_repository as repo
from voicegateway.services.retention_service import RetentionWorker
from voicegateway.services.storage_service import StorageService


@pytest.fixture
async def storage(tmp_path):
    s = StorageService(str(tmp_path / "retention-node-samples.db"))
    await s._ensure_initialized()
    return s


def _provider(project: str, days: int):
    async def provider():
        return [(project, days)]

    return provider


def _ms_days_ago(days: float) -> int:
    return int((datetime.now(UTC) - timedelta(days=days)).timestamp() * 1000)


async def _insert(storage, *, node: str, project: str, at_ms: int) -> None:
    async with storage._conn.session() as db:
        await repo.insert_samples(
            db,
            [
                repo.NodeSampleInput(
                    node=node,
                    source="livekit-server",
                    at_ms=at_ms,
                    outcome="ok",
                    project=project,
                    series_found=1,
                    values={"rooms": 2.0},
                )
            ],
        )


async def _count(storage, where: str, params: dict) -> int:
    async with storage._conn.session() as db:
        result = await db.execute(
            text(f"SELECT COUNT(*) FROM node_samples WHERE {where}"), params
        )
        return int(result.scalar() or 0)


async def test_aged_samples_are_deleted(storage) -> None:
    await _insert(storage, node="old", project="acme", at_ms=_ms_days_ago(10))
    await _insert(storage, node="young", project="acme", at_ms=_ms_days_ago(1))

    await RetentionWorker(storage, retention_provider=_provider("acme", 5)).tick_now()

    assert await _count(storage, "node = :n", {"n": "old"}) == 0
    assert await _count(storage, "node = :n", {"n": "young"}) == 1


async def test_another_projects_samples_are_untouched(storage) -> None:
    await _insert(storage, node="mine", project="acme", at_ms=_ms_days_ago(10))
    await _insert(storage, node="theirs", project="other", at_ms=_ms_days_ago(10))

    await RetentionWorker(storage, retention_provider=_provider("acme", 5)).tick_now()

    assert await _count(storage, "node = :n", {"n": "mine"}) == 0
    assert await _count(storage, "node = :n", {"n": "theirs"}) == 1


async def test_sample_prune_is_idempotent_and_batched(storage) -> None:
    for i in range(120):
        await _insert(storage, node=f"n{i}", project="acme", at_ms=_ms_days_ago(10))
    worker = RetentionWorker(
        storage, retention_provider=_provider("acme", 5), batch_size=50
    )
    first = await worker.tick_now()
    second = await worker.tick_now()
    assert first["acme"] >= 120
    assert second["acme"] == 0
    assert await _count(storage, "project = :p", {"p": "acme"}) == 0
