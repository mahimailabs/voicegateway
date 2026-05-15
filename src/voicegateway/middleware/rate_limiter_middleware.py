"""Per-provider rate limiting."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict

from voicegateway.middleware.base_middleware import MiddlewareError

logger = logging.getLogger(__name__)


class RateLimitExceeded(MiddlewareError):
    """Raised when a provider's rate limit is exceeded."""


class RateLimiter:
    """Token bucket rate limiter per provider."""

    def __init__(self, limits: dict[str, dict[str, int]]):
        """Initialize with rate limits config."""
        self._limits = limits
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def acquire(self, provider: str) -> None:
        """Acquire a rate limit token for the provider."""
        if provider not in self._limits:
            return

        rpm = self._limits[provider].get("requests_per_minute", 0)
        if rpm <= 0:
            return

        async with self._lock:
            now = time.time()
            window_start = now - 60.0

            # Clean old entries
            self._buckets[provider] = [
                t for t in self._buckets[provider] if t > window_start
            ]

            if len(self._buckets[provider]) >= rpm:
                raise RateLimitExceeded(
                    f"Rate limit exceeded for {provider}: {rpm} requests per minute"
                )

            self._buckets[provider].append(now)
