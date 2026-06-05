"""Tests for IngestRateLimiter (Phase 3, Step 1)."""

from __future__ import annotations

from voicegateway.services.ingest_rate_limiter import IngestRateLimiter


class _Clock:
    """Mutable monotonic clock for deterministic token-bucket tests."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_allows_up_to_burst_then_blocks() -> None:
    clock = _Clock()
    rl = IngestRateLimiter(requests_per_minute=60, burst=5, monotonic=clock)
    # Five tokens are available at t0; the sixth in the same instant is blocked.
    assert [rl.check("k") for _ in range(5)] == [None, None, None, None, None]
    retry = rl.check("k")
    assert retry is not None
    assert retry > 0


def test_retry_after_seconds_is_deficit_over_rate() -> None:
    clock = _Clock()
    # 60 rpm is one token per second; burst 1 means a full-token deficit -> 1.0s.
    rl = IngestRateLimiter(requests_per_minute=60, burst=1, monotonic=clock)
    assert rl.check("k") is None
    assert rl.check("k") == 1.0
    # 120 rpm is two tokens per second -> 0.5s for the same deficit.
    rl2 = IngestRateLimiter(requests_per_minute=120, burst=1, monotonic=clock)
    assert rl2.check("k") is None
    assert rl2.check("k") == 0.5


def test_refills_over_time() -> None:
    clock = _Clock()
    rl = IngestRateLimiter(requests_per_minute=60, burst=1, monotonic=clock)
    assert rl.check("k") is None
    assert rl.check("k") is not None  # exhausted
    clock.advance(1.0)  # one token per second refills exactly one token
    assert rl.check("k") is None


def test_keys_are_isolated() -> None:
    clock = _Clock()
    rl = IngestRateLimiter(requests_per_minute=60, burst=1, monotonic=clock)
    assert rl.check("a") is None
    assert rl.check("a") is not None  # a exhausted
    assert rl.check("b") is None  # b is unaffected


def test_zero_rpm_is_unlimited() -> None:
    clock = _Clock()
    rl = IngestRateLimiter(requests_per_minute=0, burst=0, monotonic=clock)
    assert all(rl.check("k") is None for _ in range(100))


def test_idle_full_buckets_are_reaped() -> None:
    clock = _Clock()
    rl = IngestRateLimiter(requests_per_minute=60, burst=1, max_keys=1, monotonic=clock)
    assert rl.check("a") is None  # a now holds zero tokens
    clock.advance(5.0)  # a would refill to full (burst 1) while idle
    assert rl.check("b") is None  # size 2 > max_keys 1 triggers a reap of full buckets
    assert "a" not in rl._buckets
    assert "b" in rl._buckets
