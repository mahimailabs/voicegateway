"""Regressions for #279 (duplicate provider chips) and #280 (inflated latency).

Both bugs were reported from one dashboard screenshot and both are read-side
symptoms of what the write side stored, so they are pinned here at the
repository boundary: seed rows that look like the reported data, then assert
the query answers what an operator would expect.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import text

from voicegateway.repository.latency_repository import get_latency_stats
from voicegateway.repository.session_repository import get_session
from voicegateway.tests.server._telemetry_harness import _Harness


@pytest.fixture
async def db():
    """Yield a session against a throwaway SQLite file, migrations applied.

    Wraps ``_Harness`` so the temp DB and the ``VOICEGW_DB_PATH`` it sets are
    both torn down even when a test raises; leaking that env var would point
    the rest of the suite at a deleted file.
    """
    harness = _Harness()
    try:
        async with harness.gateway.storage.session() as session:
            yield session
    finally:
        harness.cleanup()


async def _seed_session(db, session_id: str) -> None:
    await db.execute(
        text(
            "INSERT INTO sessions (id, project, started_at) "
            "VALUES (:id, 'p', CURRENT_TIMESTAMP)"
        ),
        {"id": session_id},
    )


async def _seed_request(db, **kw) -> None:
    row = {
        "id": kw["id"],
        "timestamp": kw.get("timestamp", time.time()),
        "session_id": kw.get("session_id"),
        "modality": kw.get("modality", "llm"),
        "provider": kw.get("provider", "cartesia"),
        "model_id": kw.get("model_id", "cartesia/sonic"),
        "project": "p",
        "status": kw.get("status", "success"),
        "cost_usd": 0.0,
        "ttfb_ms": kw.get("ttfb_ms"),
        "total_latency_ms": kw.get("total_latency_ms"),
    }
    await db.execute(
        text(
            "INSERT INTO requests (id, timestamp, session_id, modality, provider, "
            "model_id, project, status, cost_usd, ttfb_ms, total_latency_ms) "
            "VALUES (:id, :timestamp, :session_id, :modality, :provider, "
            ":model_id, :project, :status, :cost_usd, :ttfb_ms, :total_latency_ms)"
        ),
        row,
    )


# ---------------------------------------------------------------------------
# #279: one provider, one chip
# ---------------------------------------------------------------------------


async def test_mixed_spellings_collapse_to_one_provider(db):
    """The reported screenshot: six chips for three providers."""
    await _seed_session(db, "s-279")
    for i, provider in enumerate(
        ["Cartesia", "cartesia", "Deepgram", "deepgram", "Gemini", "google"]
    ):
        await _seed_request(db, id=f"r{i}", session_id="s-279", provider=provider)
    await db.commit()

    row = await get_session(db, "s-279")

    assert row is not None
    assert row["providers"] == ["cartesia", "deepgram", "google"]


async def test_empty_provider_never_becomes_a_chip(db):
    """EOU and replay-state rows carry provider=''; they are not providers."""
    await _seed_session(db, "s-eou")
    await _seed_request(db, id="r-llm", session_id="s-eou", provider="cartesia")
    await _seed_request(
        db, id="r-eou", session_id="s-eou", provider="", modality="eou", model_id=""
    )
    await _seed_request(
        db, id="r-ws", session_id="s-eou", provider="   ", modality="state"
    )
    await db.commit()

    row = await get_session(db, "s-eou")

    assert row is not None
    assert row["providers"] == ["cartesia"]
    assert "" not in row["providers"]


# ---------------------------------------------------------------------------
# #280: latency counts only what the caller experienced
# ---------------------------------------------------------------------------


async def test_cancelled_generations_do_not_inflate_latency(db):
    """The reported symptom: a fast model reported slow.

    Two real calls at 100ms, one cancelled call at 5000ms whose first-token and
    total coincide because the stream was torn down. Before the fix the average
    was 1733ms, which is the shape the reporter saw.
    """
    await _seed_request(
        db,
        id="ok1",
        model_id="cerebras/gpt-oss-120b",
        ttfb_ms=100.0,
        total_latency_ms=900.0,
    )
    await _seed_request(
        db,
        id="ok2",
        model_id="cerebras/gpt-oss-120b",
        ttfb_ms=100.0,
        total_latency_ms=900.0,
    )
    await _seed_request(
        db,
        id="cancelled1",
        model_id="cerebras/gpt-oss-120b",
        status="cancelled",
        ttfb_ms=5000.0,
        total_latency_ms=5000.0,
    )
    await db.commit()

    stats = await get_latency_stats(db, period="all")

    entry = stats["cerebras/gpt-oss-120b"]
    assert entry["request_count"] == 2
    assert entry["avg_ttfb_ms"] == pytest.approx(100.0)


async def test_errored_rows_measure_the_failure_not_the_model(db):
    """An errored row times out or fails, so it measures neither model.

    Sibling of the cancelled case: same exclusion, different reason. A 30s
    timeout says nothing about how fast the model answers when it answers.
    """
    await _seed_request(
        db, id="ok", model_id="m/x", ttfb_ms=120.0, total_latency_ms=400.0
    )
    await _seed_request(
        db,
        id="err",
        model_id="m/x",
        status="error",
        ttfb_ms=30000.0,
        total_latency_ms=30000.0,
    )
    await db.commit()

    stats = await get_latency_stats(db, period="all")

    assert stats["m/x"]["request_count"] == 1
    assert stats["m/x"]["avg_ttfb_ms"] == pytest.approx(120.0)


async def test_fallback_rows_are_kept(db):
    """A fallback was really served, so it is really the model's latency."""
    await _seed_request(
        db, id="ok", model_id="m/y", ttfb_ms=100.0, total_latency_ms=200.0
    )
    await _seed_request(
        db,
        id="fb",
        model_id="m/y",
        status="fallback",
        ttfb_ms=300.0,
        total_latency_ms=400.0,
    )
    await db.commit()

    stats = await get_latency_stats(db, period="all")

    assert stats["m/y"]["request_count"] == 2
    assert stats["m/y"]["avg_ttfb_ms"] == pytest.approx(200.0)


async def test_rows_with_null_status_are_kept(db):
    """Pre-existing rows predate the status column default.

    ``status NOT IN (...)`` is NULL for a NULL status, which is not true, so a
    bare NOT IN would have silently dropped every historical row. COALESCE is
    what keeps them.
    """
    await _seed_request(
        db,
        id="old",
        model_id="m/z",
        status=None,
        ttfb_ms=150.0,
        total_latency_ms=300.0,
    )
    await db.commit()

    stats = await get_latency_stats(db, period="all")

    assert stats["m/z"]["request_count"] == 1
    assert stats["m/z"]["avg_ttfb_ms"] == pytest.approx(150.0)
