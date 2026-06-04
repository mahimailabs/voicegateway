"""Phase 2: agents_repository aggregates + per-agent p95."""

from __future__ import annotations

import time
import uuid

import pytest

from voicegateway.models.request_model import RequestRecord
from voicegateway.repository import agents_repository as agents
from voicegateway.services.storage_service import StorageService


def _rec(
    agent: str | None,
    *,
    cost: float = 0.01,
    status: str = "success",
    total: float = 200.0,
) -> RequestRecord:
    return RequestRecord(
        id=str(uuid.uuid4()),
        timestamp=time.time(),
        modality="llm",
        model_id="openai/gpt-4o-mini",
        provider="openai",
        project="fleet",
        cost_usd=cost,
        status=status,
        ttfb_ms=100.0,
        total_latency_ms=total,
        agent_id=agent,
        session_id=f"vg-{agent}-{uuid.uuid4()}",
    )


async def _populate(storage: StorageService, records: list[RequestRecord]) -> None:
    for r in records:
        await storage.log_request(r)


async def test_list_agents_aggregates(tmp_path):
    storage = StorageService(str(tmp_path / "agents.db"))
    await _populate(
        storage,
        [
            _rec("agent-x", cost=0.01, status="success"),
            _rec("agent-x", cost=0.02, status="error"),
            _rec("agent-y", cost=0.05),
        ],
    )
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        rows = await agents.list_agents(db)

    by_id = {r.agent_id: r for r in rows}
    assert set(by_id) == {"agent-x", "agent-y"}
    assert by_id["agent-x"].request_count == 2
    assert by_id["agent-x"].total_cost_usd == pytest.approx(0.03)
    assert by_id["agent-x"].error_rate == pytest.approx(0.5)
    assert by_id["agent-x"].last_seen is not None


async def test_list_agents_excludes_null_agent(tmp_path):
    storage = StorageService(str(tmp_path / "agents2.db"))
    rec = _rec("agent-x")
    rec.agent_id = None
    await storage.log_request(rec)
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        rows = await agents.list_agents(db)
    assert rows == []


async def test_get_agent(tmp_path):
    storage = StorageService(str(tmp_path / "agents3.db"))
    await storage.log_request(_rec("agent-x"))
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        found = await agents.get_agent(db, "agent-x")
        missing = await agents.get_agent(db, "nope")
    assert found is not None
    assert found.agent_id == "agent-x"
    assert missing is None


async def test_agent_latency_p95(tmp_path):
    storage = StorageService(str(tmp_path / "agents4.db"))
    for total in (100.0, 200.0, 300.0):
        await storage.log_request(_rec("agent-x", total=total))
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        p95 = await agents.agent_latency_p95(db)
    assert "agent-x" in p95
    assert p95["agent-x"] >= 200.0  # p95 of [100, 200, 300]
