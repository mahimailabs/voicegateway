"""Tests for additional middleware components."""

import logging
import time

import pytest

from voicegateway.middleware.latency_monitor import LatencyMonitor
from voicegateway.middleware.logger import RequestLogger
from voicegateway.middleware.rate_limiter import RateLimiter, RateLimitExceeded


def test_latency_monitor_basic():
    monitor = LatencyMonitor(ttfb_warning_ms=1000)
    timer = monitor.start()
    time.sleep(0.005)
    timer.mark_first_byte()
    time.sleep(0.005)
    m = timer.finish("test/model")
    assert m.ttfb_ms > 0
    assert m.total_ms >= m.ttfb_ms
    assert m.model_id == "test/model"


def test_latency_monitor_warning(caplog):
    monitor = LatencyMonitor(ttfb_warning_ms=1)
    timer = monitor.start()
    time.sleep(0.010)
    with caplog.at_level(logging.WARNING):
        timer.mark_first_byte()
    timer.finish("slow/model")


def test_latency_monitor_no_first_byte():
    monitor = LatencyMonitor()
    timer = monitor.start()
    time.sleep(0.005)
    m = timer.finish("test")
    # When first_byte not marked, ttfb = total
    assert m.ttfb_ms >= 0
    assert m.total_ms >= 0


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


def test_request_logger_log_request(caplog):
    logger = RequestLogger()
    with caplog.at_level(logging.INFO, logger="gateway.requests"):
        logger.log_request("openai/gpt-4o-mini", "llm")
    assert any("gpt-4o-mini" in r.message for r in caplog.records)


def test_request_logger_log_response(caplog):
    logger = RequestLogger()
    with caplog.at_level(logging.INFO, logger="gateway.requests"):
        logger.log_response("deepgram/nova-3", "stt", 150.0, 0.0043)
    assert any("nova-3" in r.message for r in caplog.records)


def test_request_logger_log_fallback(caplog):
    logger = RequestLogger()
    with caplog.at_level(logging.WARNING, logger="gateway.requests"):
        logger.log_fallback("openai/gpt-4.1-mini", "groq/llama-3.1-70b", "timeout")
    assert any("FALLBACK" in r.message for r in caplog.records)


def test_request_logger_log_error(caplog):
    logger = RequestLogger()
    with caplog.at_level(logging.ERROR, logger="gateway.requests"):
        logger.log_error("test/model", "connection refused")
    assert any("connection refused" in r.message for r in caplog.records)
