"""Phase 3, Step 5: agent_observations roll_up + read paths."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from voicegateway.models.request_model import RequestRecord
from voicegateway.repository import agent_observations_repository as agent_obs
from voicegateway.services.storage_service import StorageService


@pytest.fixture
async def storage(tmp_path):
    s = StorageService(str(tmp_path / "agentobs.db"))
    await s._ensure_initialized()
    return s


async def _seed(
    storage,
    rid: str,
    agent_id: str | None,
    *,
    cost: float = 0.0,
    status: str = "success",
    latency: float | None = None,
    days_ago: float = 0.0,
) -> None:
    ts = (datetime.now(UTC) - timedelta(days=days_ago)).timestamp()
    await storage.log_request(
        RequestRecord(
            id=rid,
            timestamp=ts,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            project="acme",
            cost_usd=cost,
            status=status,
            total_latency_ms=latency,
            agent_id=agent_id,
        )
    )


async def _rollup(storage, **kw) -> int:
    async with storage._conn.session() as db:
        return await agent_obs.roll_up(db, **kw)


async def _read_agents(storage, **kw):
    async with storage._conn.session() as db:
        return await agent_obs.read_agents(db, **kw)


async def _read_unattr(storage):
    async with storage._conn.session() as db:
        return await agent_obs.read_unattributed(db)


async def test_roll_up_groups_by_agent(storage) -> None:
    await _seed(storage, "a1", "agent-a", cost=0.01, latency=100)
    await _seed(storage, "a2", "agent-a", cost=0.01, latency=200, status="error")
    await _seed(storage, "a3", "agent-a", cost=0.01, latency=300)
    await _seed(storage, "b1", "agent-b", cost=0.05, latency=50)
    await _seed(storage, "u1", None, cost=0.02)
    await _seed(storage, "u2", None, cost=0.02)

    assert await _rollup(storage) == 3  # agent-a, agent-b, unattributed

    agents = {r.agent_id: r for r in await _read_agents(storage)}
    assert agents["agent-a"].request_count == 3
    assert agents["agent-a"].error_count == 1
    assert agents["agent-a"].total_cost_usd == pytest.approx(0.03)
    assert agents["agent-a"].p95_ms is not None
    assert agents["agent-a"].p50_ms is not None
    assert agents["agent-a"].p95_ms >= agents["agent-a"].p50_ms
    assert agents["agent-b"].request_count == 1

    unattr = await _read_unattr(storage)
    assert unattr is not None
    assert unattr.agent_id is None
    assert unattr.request_count == 2
    assert unattr.total_cost_usd == pytest.approx(0.04)


async def test_window_excludes_out_of_range_rows(storage) -> None:
    await _seed(storage, "in", "agent-a", days_ago=0.04)  # ~1h ago
    await _seed(storage, "out", "agent-a", days_ago=1.25)  # ~30h ago
    await _seed(storage, "u-in", None, days_ago=0.04)
    await _seed(storage, "u-out", None, days_ago=1.25)

    await _rollup(storage, window_minutes=24 * 60)

    agents = {r.agent_id: r for r in await _read_agents(storage)}
    assert agents["agent-a"].request_count == 1  # the out-of-window row is excluded
    unattr = await _read_unattr(storage)
    assert unattr is not None and unattr.request_count == 1


async def test_null_latency_group_has_null_percentiles(storage) -> None:
    await _seed(storage, "c1", "agent-c", latency=None)
    await _rollup(storage)
    agents = {r.agent_id: r for r in await _read_agents(storage)}
    assert agents["agent-c"].p50_ms is None
    assert agents["agent-c"].p95_ms is None


async def test_roll_up_replaces_table_no_duplicates(storage) -> None:
    await _seed(storage, "a1", "agent-a", latency=100)
    await _rollup(storage)
    await _rollup(storage)  # second pass must replace, not append
    agents = await _read_agents(storage)
    assert len([r for r in agents if r.agent_id == "agent-a"]) == 1


async def test_roll_up_commits_exactly_once(storage) -> None:
    await _seed(storage, "a1", "agent-a", latency=100)
    async with storage._conn.session() as db:
        calls = {"n": 0}
        original = db.commit

        async def counting(*args, **kwargs):
            calls["n"] += 1
            await original()

        db.commit = counting  # type: ignore[method-assign]
        await agent_obs.roll_up(db)
        assert calls["n"] == 1


async def test_read_agents_limit_and_query(storage) -> None:
    await _seed(storage, "a1", "agent-a", days_ago=0.03)
    await _seed(storage, "b1", "agent-b", days_ago=0.02)
    await _seed(storage, "c1", "other-c", days_ago=0.01)
    await _rollup(storage)

    limited = await _read_agents(storage, limit=2)
    assert len(limited) == 2
    # last_seen DESC: other-c is most recent.
    assert limited[0].agent_id == "other-c"

    filtered = await _read_agents(storage, query="agent")
    assert {r.agent_id for r in filtered} == {"agent-a", "agent-b"}
