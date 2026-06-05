"""Per-key token-bucket rate limiter for the fleet ingest endpoint.

Unlike ``middleware.rate_limiter_middleware.RateLimiter`` (a per-provider
sliding window on the gateway call path), this limits HTTP callers of
``POST /v1/ingest`` by caller identity (virtual key, then static-API-key
hash, then client IP). It is a plain in-process token bucket: the collector
is a single writer, so no shared store is needed.

``check`` is synchronous and does no I/O, so on the single-threaded event
loop a call is atomic (no interleaving), which is why it needs no lock.
"""

from __future__ import annotations

import time
from collections.abc import Callable

__all__ = ["IngestRateLimiter"]


class IngestRateLimiter:
    """Token bucket keyed by caller identity.

    ``requests_per_minute`` sets the refill rate and ``burst`` the bucket
    ceiling. ``requests_per_minute <= 0`` disables limiting (every call is
    allowed). Idle buckets that have refilled to full are reaped once the
    map exceeds ``max_keys`` so memory stays bounded.
    """

    def __init__(
        self,
        *,
        requests_per_minute: int,
        burst: int,
        max_keys: int = 10_000,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._rpm = requests_per_minute
        self._rate_per_sec = requests_per_minute / 60.0
        self._burst = float(burst)
        self._max_keys = max_keys
        self._monotonic = monotonic
        # key -> (tokens, last_refill_monotonic)
        self._buckets: dict[str, tuple[float, float]] = {}

    def check(self, key: str) -> float | None:
        """Spend one token for ``key``.

        Returns ``None`` when the call is allowed, otherwise the number of
        seconds until the next token (a Retry-After hint).
        """
        if self._rpm <= 0 or self._rate_per_sec <= 0:
            return None  # limiting disabled
        now = self._monotonic()
        tokens, last = self._buckets.get(key, (self._burst, now))
        tokens = min(self._burst, tokens + (now - last) * self._rate_per_sec)
        if tokens >= 1.0:
            self._buckets[key] = (tokens - 1.0, now)
            self._maybe_reap(now)
            return None
        self._buckets[key] = (tokens, now)
        deficit = 1.0 - tokens
        return deficit / self._rate_per_sec

    def _maybe_reap(self, now: float) -> None:
        """Drop buckets that have refilled to full (idle) when over capacity."""
        if len(self._buckets) <= self._max_keys:
            return
        stale = [
            k
            for k, (tokens, last) in self._buckets.items()
            if min(self._burst, tokens + (now - last) * self._rate_per_sec)
            >= self._burst
        ]
        for k in stale:
            del self._buckets[k]
