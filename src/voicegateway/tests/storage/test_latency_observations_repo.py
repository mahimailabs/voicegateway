"""Tests for ``latency_observations`` (REQ-VG-ROUTE-002 + -003)."""

from __future__ import annotations

import time

import pytest
from sqlalchemy import text

from voicegateway.models.request_model import RequestRecord
from voicegateway.repository import latency_observations_repository as lor
from voicegateway.storage.sqlite import SQLiteStorage


@pytest.fixture
async def storage(tmp_path):
    yield SQLiteStorage(db_path=str(tmp_path / "lor.db"))


def _req(
    provider: str, modality: str, latency: float, *, idx: int = 0
) -> RequestRecord:
    return RequestRecord(
        id=f"r-{provider}-{modality}-{idx}-{latency}",
        timestamp=time.time() - idx * 30.0,
        project="default",
        modality=modality,
        model_id=f"{provider}/test",
        provider=provider,
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


async def test_roll_up_aggregates_per_group(storage) -> None:
    for i, (prov, mod, lat) in enumerate(
        [
            ("deepgram", "stt", 180),
            ("deepgram", "stt", 220),
            ("openai", "llm", 300),
            ("cartesia", "tts", 100),
        ]
    ):
        await storage.log_request(_req(prov, mod, lat, idx=i))

    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        n = await lor.roll_up(db, window_minutes=60 * 24)
        assert n == 3
        rows = await lor.read_all(db)

    by_key = {(r.provider, r.modality): r for r in rows}
    assert by_key[("deepgram", "stt")].sample_count == 2
    assert by_key[("openai", "llm")].sample_count == 1
    assert by_key[("cartesia", "tts")].sample_count == 1


async def test_roll_up_computes_p50_p95(storage) -> None:
    for i, lat in enumerate([100, 150, 200, 250, 300]):
        await storage.log_request(_req("dg", "stt", lat, idx=i))
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        await lor.roll_up(db)
        obs = await lor.get_one(db, project_id="default", provider="dg", modality="stt")
    assert obs is not None
    assert obs.p50_ms == 200
    assert obs.p95_ms is not None and obs.p95_ms >= obs.p50_ms


async def test_roll_up_empty_db(storage) -> None:
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        n = await lor.roll_up(db)
        assert n == 0
        assert await lor.read_all(db) == []


async def test_roll_up_idempotent_replace(storage) -> None:
    """Re-running roll_up replaces the snapshot atomically."""
    await storage.log_request(_req("dg", "stt", 100, idx=0))
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        await lor.roll_up(db)
        n1 = len(await lor.read_all(db))
        await lor.roll_up(db)
        n2 = len(await lor.read_all(db))
    assert n1 == n2 == 1


async def test_roll_up_excludes_null_latency(storage) -> None:
    await storage.log_request(_req("dg", "stt", 100, idx=0))
    rec = _req("dg", "stt", 200, idx=1)
    rec = RequestRecord(**{**rec.__dict__, "total_latency_ms": None})
    await storage.log_request(rec)

    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        await lor.roll_up(db)
        obs = await lor.get_one(db, project_id="default", provider="dg", modality="stt")
    assert obs is not None
    assert obs.sample_count == 1


async def test_get_for_project_scopes(storage) -> None:
    """get_for_project returns rows for one project only."""
    await storage.log_request(_req("dg", "stt", 100, idx=0))
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        # Sneak in a row for a different project via direct INSERT.
        await db.execute(
            text(
                "INSERT INTO latency_observations "
                "(project_id, provider, modality, p50_ms, p95_ms, sample_count, "
                " window_start, window_end) "
                "VALUES (:project, :provider, :modality, :p50, :p95, :sc, "
                " :ws, :we)"
            ),
            {
                "project": "other",
                "provider": "dg",
                "modality": "stt",
                "p50": 999,
                "p95": 999,
                "sc": 1,
                "ws": "x",
                "we": "y",
            },
        )
        await db.commit()
        await lor.roll_up(db)  # Clears the snapshot; "other" lost.

        # Manually seed an "other" row again post-rollup.
        await db.execute(
            text(
                "INSERT INTO latency_observations "
                "(project_id, provider, modality, p50_ms, p95_ms, sample_count, "
                " window_start, window_end) "
                "VALUES (:project, :provider, :modality, :p50, :p95, :sc, "
                " :ws, :we)"
            ),
            {
                "project": "other",
                "provider": "dg",
                "modality": "stt",
                "p50": 999,
                "p95": 999,
                "sc": 1,
                "ws": "x",
                "we": "y",
            },
        )
        await db.commit()

        default_rows = await lor.get_for_project(db, "default")
        other_rows = await lor.get_for_project(db, "other")

    assert all(r.project_id == "default" for r in default_rows)
    assert len(other_rows) == 1 and other_rows[0].provider == "dg"


async def test_get_one_returns_none_for_missing(storage) -> None:
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        assert (
            await lor.get_one(
                db, project_id="default", provider="missing", modality="stt"
            )
            is None
        )
