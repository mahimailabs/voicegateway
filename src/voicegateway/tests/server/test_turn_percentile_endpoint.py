"""A percentile over turns, so a remote drift gate pins the same mathematics.

`GET /api/metrics` reports `response_speed_ms` as the MEAN OF PER-SESSION
PERCENTILES. `voicegw baseline pin` computes a percentile over all turns. Those
are different statistics wearing one name, and a baseline pinned locally and
checked against a collector would have compared them while looking exactly like
a comparison.

The concrete divergence, which is the whole reason this endpoint exists: on two
sessions with turn latencies [100,110,120,130,900] and [200,210,220], a p95 over
turns is 662 and the mean of per-session p95s is 482. Both are defensible
numbers. Only one of them is a p95.
"""

from __future__ import annotations

import statistics
import warnings

import pytest
import yaml
from fastapi.testclient import TestClient

from voicegateway.core.gateway import Gateway
from voicegateway.middleware.turn_tracker_middleware import TurnRow
from voicegateway.repository import turns_repository as turns
from voicegateway.server.main import build_app
from voicegateway.services.storage_service import StorageService

_CFG = {
    "providers": {},
    "projects": {},
    "fallbacks": {"stt": [], "llm": [], "tts": []},
    "cost_tracking": {"enabled": True},
}

# Epoch-ms instants inside a "today" window, since turns are windowed on
# caller_speak_start_ms rather than on write time.
_NOW_MS = 1_785_661_200_000


def _turn(session_id: str, index: int, at_ms: int, speed: int) -> TurnRow:
    return TurnRow(
        session_id=session_id,
        turn_index=index,
        caller_speak_start_ms=at_ms,
        caller_speak_end_ms=at_ms + 500,
        agent_speak_start_ms=at_ms + 500 + speed,
        response_speed_ms=speed,
    )


@pytest.fixture
def storage(tmp_path):
    warnings.filterwarnings("ignore")
    return StorageService(str(tmp_path / "turns.db"))


async def _seed(storage, rows: list[TurnRow]) -> None:
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        await turns.create_turns_bulk(db, rows, tenant_id=None)


# --------------------------------------------------------------------------
# The statistic
# --------------------------------------------------------------------------


async def test_it_is_a_percentile_over_turns_not_a_mean_of_session_percentiles(
    storage,
) -> None:
    """The divergence that motivated the endpoint, asserted numerically.

    If these two ever agree the test is not proving anything, so the fixture is
    chosen to make them differ by a wide margin.
    """
    a = [100, 110, 120, 130, 900]
    b = [200, 210, 220]
    rows = [_turn("a", i, _NOW_MS + i * 1000, v) for i, v in enumerate(a)]
    rows += [_turn("b", i, _NOW_MS + i * 1000, v) for i, v in enumerate(b)]
    await _seed(storage, rows)

    async with storage._conn.session() as db:
        agg = await turns.aggregate_response_speed(db)
    await storage.aclose()

    def p95(vals):
        return statistics.quantiles(vals, n=100, method="inclusive")[94]

    over_turns = int(p95(a + b))
    mean_of_session_p95s = int((p95(a) + p95(b)) / 2)
    assert over_turns != mean_of_session_p95s, "fixture no longer distinguishes them"
    assert agg["p95_ms"] == over_turns
    assert agg["p95_ms"] != mean_of_session_p95s
    assert agg["samples"] == len(a) + len(b)


async def test_the_sample_count_travels_with_the_values(storage) -> None:
    """A percentile over three turns and one over two hundred are different
    claims, and a caller reading only the number cannot tell them apart."""
    await _seed(storage, [_turn("a", i, _NOW_MS + i * 1000, 100 + i) for i in range(3)])
    async with storage._conn.session() as db:
        agg = await turns.aggregate_response_speed(db)
    await storage.aclose()
    assert agg["samples"] == 3


# --------------------------------------------------------------------------
# The window, which is where a unit error hides
# --------------------------------------------------------------------------


async def test_the_window_excludes_turns_outside_it(storage) -> None:
    """Windowed on the turn's own instant, not on when the row was written.

    `created_at` lags by the buffer flush, which would move a turn between
    windows depending on when the agent got round to sending it.
    """
    old = _NOW_MS - 40 * 86_400_000
    await _seed(
        storage,
        [_turn("a", 0, old, 5000), _turn("a", 1, _NOW_MS, 100)],
    )
    async with storage._conn.session() as db:
        recent = await turns.aggregate_response_speed(db, since_ms=_NOW_MS - 1000)
        everything = await turns.aggregate_response_speed(db)
    await storage.aclose()
    assert recent["samples"] == 1
    assert recent["p95_ms"] == 100
    assert everything["samples"] == 2


async def test_seconds_passed_as_milliseconds_would_not_go_unnoticed(storage) -> None:
    """The unit trap, pinned.

    `resolve_window` speaks epoch SECONDS because `requests.timestamp` is
    seconds; turns are windowed on epoch MILLISECONDS. Passing seconds straight
    through puts the window in 1970 and returns every turn ever recorded, which
    reads as a healthy sample count rather than as an error.
    """
    await _seed(storage, [_turn("a", 0, _NOW_MS - 40 * 86_400_000, 5000)])
    async with storage._conn.session() as db:
        correct = await turns.aggregate_response_speed(
            db, since_ms=_NOW_MS - 86_400_000
        )
        as_if_seconds = await turns.aggregate_response_speed(
            db, since_ms=int((_NOW_MS - 86_400_000) / 1000)
        )
    await storage.aclose()
    assert correct["samples"] == 0, "the old turn should be outside a one-day window"
    assert as_if_seconds["samples"] == 1, "seconds-as-ms silently widens the window"


# --------------------------------------------------------------------------
# The route
# --------------------------------------------------------------------------


def test_the_endpoint_is_reachable_and_carries_the_sample_count(
    tmp_path, monkeypatch
) -> None:
    warnings.filterwarnings("ignore")
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    cfg = tmp_path / "voicegw.yaml"
    cfg.write_text(yaml.dump(_CFG))
    client = TestClient(build_app(Gateway(config_path=str(cfg)), enable_mcp_sse=False))
    result = client.get("/api/turns/response-speed?period=all")
    assert result.status_code == 200
    body = result.json()
    assert set(body) >= {"p50_ms", "p95_ms", "p99_ms", "samples"}
    assert body["samples"] == 0
