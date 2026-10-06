"""Tests for ``DeepgramProvider.health_check``."""

from __future__ import annotations

from unittest.mock import patch

from voicegateway.inference.providers.deepgram_provider import DeepgramProvider


async def test_health_check_returns_false_when_key_missing(monkeypatch):
    """No API key, no probe — returns False immediately."""
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    provider = DeepgramProvider({})

    with patch(
        "httpx.AsyncClient",
        side_effect=AssertionError("must not call httpx"),
    ):
        result = await provider.health_check()

    assert result is False
