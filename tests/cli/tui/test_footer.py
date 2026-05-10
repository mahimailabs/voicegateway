"""Pilot tests for the Phase-9 CounterFooter live counter row.

Covers REQ-VG-TUI-007:

- Initial render: pre-fetch text is the documented placeholder.
- Synthetic-data update: a cost change in the stub surfaces in the
  rendered counter line within the polling budget.
- Disconnect: HttpClient's ``is_connected = False`` flips the
  rendered text to ``Reconnecting to daemon...``.
- Recovery: the next successful round-trip flips back to
  ``Today: $... Requests: ...``.
- Pure formatter: ``_format`` rounds the total + aggregates per-
  modality request counts; tolerates None / missing fields.
"""

from __future__ import annotations

from typing import Any

import httpx
from textual.widgets import Static

from voicegateway.cli.tui import TUIApp
from voicegateway.cli.tui.data.http import HttpClient
from voicegateway.cli.tui.widgets.footer import (
    CounterFooter,
    _aggregate_request_count,
    _format,
)

# ---------------------------------------------------------------------------
# Pure formatter (no Textual)
# ---------------------------------------------------------------------------


def test_format_renders_total_and_aggregate_request_count() -> None:
    costs = {
        "total": 0.0524,
        "by_modality": {
            "stt": {"cost": 0.01, "request_count": 4},
            "llm": {"cost": 0.04, "request_count": 7},
        },
    }
    text = _format(costs)
    assert "$0.0524" in text
    assert "Requests: 11" in text  # 4 + 7


def test_format_falls_back_to_flat_request_count() -> None:
    costs = {"total": 0.05, "request_count": 9}
    assert "Requests: 9" in _format(costs)


def test_format_handles_missing_request_count() -> None:
    costs = {"total": 0.05}
    assert "Requests: --" in _format(costs)


def test_format_handles_none_total() -> None:
    costs = {"total": None}
    assert "$0.0000" in _format(costs)


def test_format_handles_non_dict_input() -> None:
    """Pre-fetch state passes ``None`` -- placeholder rather than crash."""
    assert _format(None) == "Today: ..."
    assert _format("nonsense") == "Today: ..."


def test_aggregate_request_count_returns_dashes_when_unknown() -> None:
    assert _aggregate_request_count({}) == "--"
    assert _aggregate_request_count({"by_modality": {}}) == "--"


# ---------------------------------------------------------------------------
# Pilot: indicator visibility + recovery
# ---------------------------------------------------------------------------


def _make_app(handler) -> TUIApp:
    inner = httpx.AsyncClient(
        base_url="http://daemon.test",
        transport=httpx.MockTransport(handler),
    )
    client = HttpClient(url="http://daemon.test", http_client=inner, poll_seconds=0.05)
    return TUIApp(client=client, is_local=False)


def _ok_handler(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "period": "today",
            "total": 0.10,
            "by_modality": {"llm": {"cost": 0.10, "request_count": 5}},
        },
    )


async def _settle(pilot: Any, ticks: int = 30) -> None:
    """Drain enough event-loop ticks to let the polling worker fire."""
    for _ in range(ticks):
        await pilot.pause(0.05)


def _footer_text(app: TUIApp) -> str:
    footer = app.query_one(CounterFooter)
    return str(footer.query_one("#counter-text", Static).renderable)


async def test_counter_footer_updates_with_synthetic_data() -> None:
    """A successful response flips the placeholder to a real summary."""
    app = _make_app(_ok_handler)
    async with app.run_test() as pilot:
        await _settle(pilot, ticks=20)
        text = _footer_text(app)
        assert "$0.1000" in text
        assert "Requests: 5" in text


async def test_counter_footer_shows_reconnecting_indicator() -> None:
    """``HttpClient.is_connected = False`` (after a ConnectError)
    flips the rendered text to ``Reconnecting to daemon...``.
    """
    state = {"fail": True}

    def handler(_request: httpx.Request) -> httpx.Response:
        if state["fail"]:
            raise httpx.ConnectError("boom")
        return _ok_handler(_request)

    app = _make_app(handler)
    async with app.run_test() as pilot:
        await _settle(pilot, ticks=20)
        assert "Reconnecting" in _footer_text(app)


async def test_counter_footer_recovery_hides_indicator() -> None:
    """When the stub stops failing, the next poll tick replaces the
    indicator with the cost summary; ``Reconnecting`` is gone.
    """
    state = {"fail": True}

    def handler(_request: httpx.Request) -> httpx.Response:
        if state["fail"]:
            raise httpx.ConnectError("boom")
        return _ok_handler(_request)

    app = _make_app(handler)
    async with app.run_test() as pilot:
        await _settle(pilot, ticks=15)
        assert "Reconnecting" in _footer_text(app)
        state["fail"] = False
        await _settle(pilot, ticks=30)
        text = _footer_text(app)
        assert "Reconnecting" not in text
        assert "Today" in text
