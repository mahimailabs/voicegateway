"""Tests for additional middleware components."""

import pytest

from voicegateway.middleware.rate_limiter_middleware import (
    RateLimiter,
    RateLimitExceeded,
)


@pytest.mark.asyncio
async def test_rate_limiter_allows_within_limit():
    limiter = RateLimiter({"openai": {"requests_per_minute": 5}})
    for _ in range(5):
        await limiter.acquire("openai")


@pytest.mark.asyncio
async def test_rate_limiter_blocks_over_limit():
    limiter = RateLimiter({"groq": {"requests_per_minute": 2}})
    await limiter.acquire("groq")
    await limiter.acquire("groq")
    with pytest.raises(RateLimitExceeded):
        await limiter.acquire("groq")


@pytest.mark.asyncio
async def test_rate_limiter_no_limit_configured():
    limiter = RateLimiter({})
    # Should not raise for any provider
    for _ in range(100):
        await limiter.acquire("unknown")


@pytest.mark.asyncio
async def test_rate_limiter_zero_limit_is_unlimited():
    limiter = RateLimiter({"test": {"requests_per_minute": 0}})
    for _ in range(100):
        await limiter.acquire("test")
