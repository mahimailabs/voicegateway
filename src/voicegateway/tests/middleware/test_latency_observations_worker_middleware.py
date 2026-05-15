"""Tests for ``LatencyObservationsWorker`` (T06)."""

from __future__ import annotations

import asyncio
import time

import pytest

from voicegateway.middleware.latency_observations_worker_middleware import (
    LatencyObservationsWorker,
)
from voicegateway.models.request_model import RequestRecord
from voicegateway.repository import latency_observations_repository as lor
from voicegateway.services.storage_service import StorageService


@pytest.fixture
async def storage(tmp_path):
    yield StorageService(db_path=str(tmp_path / "w.db"))


def _req(idx: int, latency: float = 180.0) -> RequestRecord:
    return RequestRecord(
        id=f"r-{idx}-{latency}",
        timestamp=time.time() - idx * 60.0,
        project="default",
        modality="stt",
        model_id="deepgram/test",
        provider="deepgram",
        input_units=0,
        output_units=0,
        cost_usd=0.0,
        pricing_source="t",
        ttfb_ms=None,
        total_latency_ms=latency,
        status="success",
        fallback_from=None,
        error_message=None,
        metadata=None,
        session_id=None,
    )


async def test_tick_now_runs_one_pass(storage) -> None:
    for i, lat in enumerate([200, 220, 180]):
        await storage.log_request(_req(i, lat))
    w = LatencyObservationsWorker(storage, poll_interval_seconds=0.1)
    n = await w.tick_now()
    assert n == 1  # one (provider, modality) group


async def test_start_stop_idempotent(storage) -> None:
    w = LatencyObservationsWorker(storage, poll_interval_seconds=0.1)
    await w.start()
    await w.start()  # No-op second call.
    await asyncio.sleep(0.25)  # Let at least one tick fire.
    await w.stop()
    await w.stop()  # No-op second call.


async def test_custom_window_provider(storage) -> None:
    """Window provider value flows through to roll_up."""
    await storage.log_request(_req(0, 200))
    captured: list[int] = []

    async def provider() -> int:
        captured.append(42)
        return 42

    w = LatencyObservationsWorker(
        storage, window_provider=provider, poll_interval_seconds=0.1
    )
    await w.tick_now()
    assert captured == [42]


async def test_loop_continues_on_tick_exception(storage) -> None:
    """A failing window_provider does not crash the loop; the worker"""

    async def boom() -> int:
        raise RuntimeError("boom")

    w = LatencyObservationsWorker(
        storage, window_provider=boom, poll_interval_seconds=0.1
    )
    await w.start()
    await asyncio.sleep(0.25)
    await w.stop()


async def test_negative_window_clamps_to_default(storage) -> None:
    """A negative window value gets clamped + a warning logged."""
    await storage.log_request(_req(0, 200))

    async def provider() -> int:
        return -5

    w = LatencyObservationsWorker(
        storage, window_provider=provider, poll_interval_seconds=0.1
    )
    n = await w.tick_now()
    # Default 24h window picks up the seeded request.
    assert n == 1


async def test_poll_interval_validation(storage) -> None:
    with pytest.raises(ValueError):
        LatencyObservationsWorker(storage, poll_interval_seconds=0)


async def test_empty_db_inserts_zero(storage) -> None:
    w = LatencyObservationsWorker(storage, poll_interval_seconds=0.1)
    n = await w.tick_now()
    assert n == 0
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        assert await lor.read_all(db) == []
