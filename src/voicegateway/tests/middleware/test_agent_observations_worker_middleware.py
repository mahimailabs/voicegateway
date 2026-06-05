"""Phase 3, Step 6: AgentObservationsWorker (mirrors LatencyObservationsWorker)."""

from __future__ import annotations

import asyncio
import time

import pytest

from voicegateway.middleware.agent_observations_worker_middleware import (
    AgentObservationsWorker,
)
from voicegateway.models.request_model import RequestRecord
from voicegateway.services.retention_service import RetentionWorker
from voicegateway.services.storage_service import StorageService


@pytest.fixture
async def storage(tmp_path):
    yield StorageService(db_path=str(tmp_path / "w.db"))


def _req(rid: str, agent_id: str | None, latency: float = 180.0) -> RequestRecord:
    return RequestRecord(
        id=rid,
        timestamp=time.time(),
        project="default",
        modality="llm",
        model_id="openai/gpt-4o-mini",
        provider="openai",
        cost_usd=0.0,
        total_latency_ms=latency,
        status="success",
        agent_id=agent_id,
    )


async def test_tick_now_runs_one_pass(storage) -> None:
    await storage.log_request(_req("a1", "agent-a"))
    await storage.log_request(_req("b1", "agent-b"))
    await storage.log_request(_req("u1", None))
    w = AgentObservationsWorker(storage, poll_interval_seconds=0.1)
    n = await w.tick_now()
    assert n == 3  # agent-a, agent-b, unattributed


async def test_default_poll_interval_is_900(storage) -> None:
    w = AgentObservationsWorker(storage)
    assert w._poll_interval == 900
    # Companion assertion for the retention cadence (spec decision 8).
    assert RetentionWorker(storage)._poll_interval == 3600


async def test_start_stop_idempotent(storage) -> None:
    w = AgentObservationsWorker(storage, poll_interval_seconds=0.1)
    await w.start()
    await w.start()
    await asyncio.sleep(0.25)
    await w.stop()
    await w.stop()


async def test_custom_window_provider_flows_through(storage) -> None:
    await storage.log_request(_req("a1", "agent-a"))
    captured: list[int] = []

    async def provider() -> int:
        captured.append(42)
        return 42

    w = AgentObservationsWorker(
        storage, window_provider=provider, poll_interval_seconds=0.1
    )
    await w.tick_now()
    assert captured == [42]


async def test_loop_continues_on_tick_exception(storage) -> None:
    async def boom() -> int:
        raise RuntimeError("boom")

    w = AgentObservationsWorker(
        storage, window_provider=boom, poll_interval_seconds=0.1
    )
    await w.start()
    await asyncio.sleep(0.25)
    await w.stop()


async def test_poll_interval_validation(storage) -> None:
    with pytest.raises(ValueError):
        AgentObservationsWorker(storage, poll_interval_seconds=0)


async def test_empty_db_inserts_zero(storage) -> None:
    w = AgentObservationsWorker(storage, poll_interval_seconds=0.1)
    assert await w.tick_now() == 0
