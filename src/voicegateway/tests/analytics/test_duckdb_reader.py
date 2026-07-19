"""The DuckDB read path returns identical results to the SQLite ORM path.

Seeds a real SQLite store (SQLModel create_all), then runs each dashboard
aggregation through the DuckDB-engine service and the SQLite-engine service and
asserts they agree. Also covers the opt-in gate and the fallback.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import pytest

pytest.importorskip("duckdb")

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database
from voicegateway.models.request_model import Request
from voicegateway.services.cost_service import CostService
from voicegateway.services.latency_service import LatencyService


def _approx_eq(a: Any, b: Any) -> bool:
    """Structural equality with float tolerance (SUM order differs per engine)."""
    if isinstance(a, dict):
        return (
            isinstance(b, dict)
            and a.keys() == b.keys()
            and all(_approx_eq(a[k], b[k]) for k in a)
        )
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(
            _approx_eq(x, y) for x, y in zip(a, b, strict=False)
        )
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            return a is None and b is None
        return b == pytest.approx(a, rel=1e-9, abs=1e-9)
    return a == b


@pytest.fixture
async def seeded_db_path(tmp_path):
    db_path = str(tmp_path / "analytics.db")
    db = Database(GatewayConfig(cost_tracking={"db_path": db_path}))
    await db.create_all()
    now = time.time()
    recs = [
        Request(
            id=str(uuid.uuid4()),
            timestamp=now - 3600,
            modality="stt",
            model_id="deepgram/nova-3",
            provider="deepgram",
            project="prod",
            cost_usd=0.0043,
            pricing_source="voice-prices@0.0.9",
            ttfb_ms=120.0,
            total_latency_ms=250.0,
            agent_id="a1",
            tenant_id="t1",
        ),
        Request(
            id=str(uuid.uuid4()),
            timestamp=now - 7200,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            project="prod",
            cost_usd=0.015,
            ttfb_ms=200.0,
            total_latency_ms=800.0,
            agent_id=None,
        ),
        Request(
            id=str(uuid.uuid4()),
            timestamp=now - 2 * 86400,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            project="default",
            cost_usd=0.008,
            ttfb_ms=180.0,
            total_latency_ms=600.0,
            agent_id="a2",
        ),
        Request(
            id=str(uuid.uuid4()),
            timestamp=now - 3 * 86400,
            modality="tts",
            model_id="cartesia/sonic-2",
            provider="cartesia",
            project="prod",
            cost_usd=0.0021,
            ttfb_ms=90.0,
            total_latency_ms=None,
            agent_id="a1",
        ),
        Request(
            id=str(uuid.uuid4()),
            timestamp=now - 5 * 86400,
            modality="llm",
            model_id="anthropic/claude-sonnet-4-6",
            provider="anthropic",
            project="default",
            cost_usd=0.031,
            ttfb_ms=None,
            total_latency_ms=1100.0,
            agent_id=None,
        ),
        Request(
            id=str(uuid.uuid4()),
            timestamp=now - 6 * 86400,
            modality="stt",
            model_id="deepgram/nova-3",
            provider="deepgram",
            project="prod",
            cost_usd=0.0051,
            pricing_source="voice-prices@0.0.8",
            ttfb_ms=140.0,
            total_latency_ms=300.0,
            agent_id="a2",
        ),
        Request(
            id=str(uuid.uuid4()),
            timestamp=now - 9 * 86400,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            project="default",
            cost_usd=0.012,
            ttfb_ms=210.0,
            total_latency_ms=740.0,
            agent_id="a1",
        ),
    ]
    async with db.session() as s:
        for r in recs:
            s.add(r)
        await s.commit()
    await db.dispose()
    yield db_path


def _cost(db_path: str, engine: str | None) -> CostService:
    ct: dict[str, Any] = {"db_path": db_path}
    if engine:
        ct["read_engine"] = engine
    return CostService(Database(GatewayConfig(cost_tracking=ct)))


def _lat(db_path: str, engine: str | None) -> LatencyService:
    ct: dict[str, Any] = {"db_path": db_path}
    if engine:
        ct["read_engine"] = engine
    return LatencyService(Database(GatewayConfig(cost_tracking=ct)))


async def test_cost_aggregations_match_sqlite(seeded_db_path):
    duck, lite = _cost(seeded_db_path, "duckdb"), _cost(seeded_db_path, None)
    assert duck._use_duckdb() and not lite._use_duckdb()
    for kw in ({"period": "month"}, {"period": "month", "project": "prod"}):
        assert _approx_eq(await duck.get_summary(**kw), await lite.get_summary(**kw))
        assert _approx_eq(await duck.get_by_day(**kw), await lite.get_by_day(**kw))
        assert _approx_eq(
            await duck.get_by_modality(**kw), await lite.get_by_modality(**kw)
        )
    assert _approx_eq(
        await duck.get_by_project(period="month"),
        await lite.get_by_project(period="month"),
    )
    assert _approx_eq(
        await duck.get_summary(period="month", include_pricing_source=True),
        await lite.get_summary(period="month", include_pricing_source=True),
    )


async def test_latency_percentiles_match_sqlite(seeded_db_path):
    duck, lite = _lat(seeded_db_path, "duckdb"), _lat(seeded_db_path, None)
    d = await duck.get_stats(period="month")
    s = await lite.get_stats(period="month")
    assert _approx_eq(d, s)
    # percentiles were actually computed (not the empty-input None sentinel);
    # GROUP BY order is arbitrary, so check that at least one model has them.
    assert any(v["latency_percentiles"]["p95"] is not None for v in d.values())


async def test_engine_off_never_calls_duckdb(seeded_db_path, monkeypatch):
    from voicegateway.analytics import duckdb_reader

    called = {"n": 0}
    orig = duckdb_reader.cost_summary
    monkeypatch.setattr(
        duckdb_reader,
        "cost_summary",
        lambda *a, **k: (called.__setitem__("n", called["n"] + 1), orig(*a, **k))[1],
    )
    await _cost(seeded_db_path, None).get_summary(period="month")
    assert called["n"] == 0


async def test_falls_back_to_sqlite_on_error(seeded_db_path, monkeypatch):
    from voicegateway.analytics import duckdb_reader

    def boom(*a, **k):
        raise RuntimeError("duckdb unavailable")

    monkeypatch.setattr(duckdb_reader, "cost_summary", boom)
    out = await _cost(seeded_db_path, "duckdb").get_summary(period="month")
    assert out["total"] > 0  # fell back to sqlite and still returned real data


async def test_duckdb_path_actually_executes(seeded_db_path, monkeypatch):
    """Prove DuckDB served the result (not a silent fallback): breaking the
    SQLite repo must NOT break the query when the engine is on."""
    from voicegateway.services import cost_service

    async def boom(*a, **k):
        raise AssertionError("sqlite path must not run when duckdb serves it")

    monkeypatch.setattr(cost_service.repo, "get_cost_summary", boom)
    out = await _cost(seeded_db_path, "duckdb").get_summary(period="month")
    assert out["total"] > 0


async def test_cost_where_branches_match_sqlite(seeded_db_path):
    """The tenant / agent / explicit-window WHERE branches match SQLite too."""
    duck, lite = _cost(seeded_db_path, "duckdb"), _cost(seeded_db_path, None)
    now = time.time()
    branches: tuple[dict[str, Any], ...] = (
        {"period": "month", "agent": "a1"},  # agent value branch
        {"period": "month", "agent": ""},  # agent IS NULL branch
        {"period": "month", "tenant": "t1"},  # tenant value branch
        {"period": "month", "tenant": ""},  # tenant IS NULL branch
        {"start_ts": now - 4 * 86400, "end_ts": now - 1800},  # explicit until window
    )
    for kw in branches:
        assert _approx_eq(await duck.get_summary(**kw), await lite.get_summary(**kw))
        assert _approx_eq(await duck.get_by_day(**kw), await lite.get_by_day(**kw))
        assert _approx_eq(
            await duck.get_by_project(**kw), await lite.get_by_project(**kw)
        )


async def test_pricing_source_multi_source_sorted(seeded_db_path):
    """Two distinct sources on one model concatenate in a stable, engine-
    identical order (the sorted-tokens fix)."""
    duck, lite = _cost(seeded_db_path, "duckdb"), _cost(seeded_db_path, None)
    d = await duck.get_summary(period="month", include_pricing_source=True)
    s = await lite.get_summary(period="month", include_pricing_source=True)
    assert _approx_eq(d, s)
    assert (
        d["by_model"]["deepgram/nova-3"]["pricing_source"]
        == "voice-prices@0.0.8,voice-prices@0.0.9"
    )


async def test_latency_falls_back_to_sqlite_on_error(seeded_db_path, monkeypatch):
    from voicegateway.analytics import duckdb_reader

    def boom(*a, **k):
        raise RuntimeError("duckdb unavailable")

    monkeypatch.setattr(duckdb_reader, "latency_stats", boom)
    out = await _lat(seeded_db_path, "duckdb").get_stats(period="month")
    assert any(v["latency_percentiles"]["p95"] is not None for v in out.values())


async def test_duplicate_percentiles_raise_on_both(seeded_db_path):
    """A colliding percentile list raises identically on both engines."""
    duck, lite = _lat(seeded_db_path, "duckdb"), _lat(seeded_db_path, None)
    with pytest.raises(ValueError):
        await duck.get_stats(period="month", percentiles=[50.0, 50.0])
    with pytest.raises(ValueError):
        await lite.get_stats(period="month", percentiles=[50.0, 50.0])
