"""Tests for ``CartesiaProvider.health_check``.

Cartesia's API requires a ``Cartesia-Version`` header on every request
and returns 400 without it. livekit-plugins-cartesia sets the header
internally for TTS, but our bypass health_check probe (used by
``vg_test_provider_key`` and the dashboard "Test" button) hits
``GET /voices`` with httpx directly. These tests pin the contract so a
future refactor that drops the header trips immediately.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from voicegateway.providers.cartesia_provider import CartesiaProvider


def _make_async_client_mock(status_code: int = 200) -> MagicMock:
    """Build an httpx.AsyncClient stand-in whose ``get()`` returns a
    response object with the given status_code. The MagicMock supports
    ``async with`` via the AsyncMock context-manager pattern.
    """
    response = MagicMock(status_code=status_code)
    client_instance = MagicMock()
    client_instance.get = AsyncMock(return_value=response)
    client_ctx = MagicMock()
    client_ctx.__aenter__ = AsyncMock(return_value=client_instance)
    client_ctx.__aexit__ = AsyncMock(return_value=None)
    return client_ctx, client_instance


async def test_health_check_sends_cartesia_version_header():
    """The fix the test guards: every health_check probe must include
    ``Cartesia-Version`` (any non-empty value). Catches a regression
    where someone strips the header during a refactor.
    """
    provider = CartesiaProvider({"api_key": "sk_car_test"})
    client_ctx, client = _make_async_client_mock(200)
    with patch("httpx.AsyncClient", return_value=client_ctx):
        result = await provider.health_check()

    assert result is True
    # Confirm the GET happened with the header present and non-empty.
    assert client.get.await_count == 1
    _, kwargs = client.get.await_args
    headers = kwargs["headers"]
    assert "Cartesia-Version" in headers
    assert headers["Cartesia-Version"], "Cartesia-Version header is empty"
    # X-API-Key should still be there too.
    assert headers["X-API-Key"] == "sk_car_test"


async def test_health_check_returns_false_on_400():
    """Even a 400 (e.g. an unrecognised version date) must surface as
    an unhealthy probe, not as an exception.
    """
    provider = CartesiaProvider({"api_key": "sk_car_test"})
    client_ctx, _ = _make_async_client_mock(400)
    with patch("httpx.AsyncClient", return_value=client_ctx):
        result = await provider.health_check()
    assert result is False


async def test_health_check_returns_false_when_key_missing(monkeypatch):
    """No API key, no probe — returns False immediately without
    attempting the network call.
    """
    monkeypatch.delenv("CARTESIA_API_KEY", raising=False)
    provider = CartesiaProvider({})
    # If the code path is correct, httpx.AsyncClient is never invoked.
    with patch("httpx.AsyncClient", side_effect=AssertionError("must not call httpx")):
        result = await provider.health_check()
    assert result is False


async def test_health_check_swallows_network_errors():
    """A genuine network failure (DNS, connection refused, timeout)
    surfaces as False, not an exception.
    """
    import httpx

    provider = CartesiaProvider({"api_key": "sk_car_test"})
    client_ctx = MagicMock()
    client_ctx.__aenter__ = AsyncMock(
        side_effect=httpx.ConnectError("DNS lookup failed")
    )
    client_ctx.__aexit__ = AsyncMock(return_value=None)
    with patch("httpx.AsyncClient", return_value=client_ctx):
        result = await provider.health_check()
    assert result is False
