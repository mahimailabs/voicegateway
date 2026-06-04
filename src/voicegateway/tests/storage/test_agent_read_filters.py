"""Phase 2: agent filter on the cost / logs / latency read paths."""

from __future__ import annotations

import time
import uuid

import pytest

from voicegateway.models.request_model import RequestRecord
from voicegateway.services.storage_service import StorageService


def _rec(agent: str, *, cost: float = 0.01, ttfb: float = 100.0, total: float = 200.0):
    return RequestRecord(
        id=str(uuid.uuid4()),
        timestamp=time.time(),
        modality="llm",
        model_id="openai/gpt-4o-mini",
        provider="openai",
        project="fleet",
        cost_usd=cost,
        ttfb_ms=ttfb,
        total_latency_ms=total,
        agent_id=agent,
        session_id=f"vg-{agent}-{uuid.uuid4()}",
    )


async def test_cost_summary_filters_by_agent(tmp_path):
    storage = StorageService(str(tmp_path / "cost.db"))
    await storage.log_request(_rec("agent-x", cost=0.01))
    await storage.log_request(_rec("agent-y", cost=0.05))
    summary = await storage.get_cost_summary(period="today", agent="agent-x")
    assert summary["total"] == pytest.approx(0.01)


async def test_recent_requests_filters_by_agent(tmp_path):
    storage = StorageService(str(tmp_path / "logs.db"))
    await storage.log_request(_rec("agent-x"))
    await storage.log_request(_rec("agent-y"))
    rows = await storage.get_recent_requests(agent="agent-x")
    assert rows
    assert all(r["agent_id"] == "agent-x" for r in rows)


async def test_latency_stats_filters_by_agent(tmp_path):
    storage = StorageService(str(tmp_path / "lat.db"))
    await storage.log_request(_rec("agent-x"))
    await storage.log_request(_rec("agent-x"))
    await storage.log_request(_rec("agent-y"))
    stats = await storage.get_latency_stats(period="today", agent="agent-x")
    assert stats["openai/gpt-4o-mini"]["request_count"] == 2
