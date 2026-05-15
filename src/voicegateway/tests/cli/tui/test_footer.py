"""Pilot tests for the Phase-9 CounterFooter live counter row."""

from __future__ import annotations

import os
import time as _time
from pathlib import Path
from typing import Any

import httpx
from textual.widgets import Static

from voicegateway.cli.tui import TUIApp
from voicegateway.cli.tui.data.http import HttpClient
from voicegateway.cli.tui.widgets.counter_footer_widget import (
    CounterFooter,
    _aggregate_request_count,
    _format,
    _format_age,  # noqa: F401
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
# Local-mode age indicator (REQ-VG-TUI-008)
# ---------------------------------------------------------------------------


def test_format_age_seconds_bucket(tmp_path: Path) -> None:
    db = tmp_path / "voicegw.db"
    db.write_text("seed")
    age = _format_age(db)
    assert age is not None
    assert "as of" in age
    assert "s ago" in age and "min" not in age


def test_format_age_minutes_bucket(tmp_path: Path) -> None:
    db = tmp_path / "voicegw.db"
    db.write_text("seed")
    five_min_ago = _time.time() - 5 * 60
    os.utime(db, (five_min_ago, five_min_ago))
    age = _format_age(db)
    assert age == "as of 5 min ago"


def test_format_age_hours_bucket(tmp_path: Path) -> None:
    db = tmp_path / "voicegw.db"
    db.write_text("seed")
    two_hours_ago = _time.time() - 2 * 3600
    os.utime(db, (two_hours_ago, two_hours_ago))
    age = _format_age(db)
    assert age == "as of 2h ago"


def test_format_age_days_bucket(tmp_path: Path) -> None:
    db = tmp_path / "voicegw.db"
    db.write_text("seed")
    three_days_ago = _time.time() - 3 * 86400
    os.utime(db, (three_days_ago, three_days_ago))
    age = _format_age(db)
    assert age == "as of 3d ago"


def test_format_age_returns_none_on_missing_file(tmp_path: Path) -> None:
    """Pre-init / wiped state: the helper returns None rather than"""
    missing = tmp_path / "no-such-file.db"
    assert _format_age(missing) is None


def test_format_appends_age_suffix_in_local_mode(tmp_path: Path) -> None:
    db = tmp_path / "voicegw.db"
    db.write_text("seed")
    seven_min_ago = _time.time() - 7 * 60
    os.utime(db, (seven_min_ago, seven_min_ago))
    text = _format({"total": 0.05, "request_count": 3}, is_local=True, db_path=db)
    assert "(as of 7 min ago)" in text


def test_format_omits_age_suffix_in_gateway_mode(tmp_path: Path) -> None:
    """Gateway mode serves live data; the suffix is misleading here"""
    db = tmp_path / "voicegw.db"
    db.write_text("seed")
    text = _format({"total": 0.05, "request_count": 3}, is_local=False, db_path=db)
    assert "(as of" not in text


def test_format_omits_age_suffix_when_db_path_missing() -> None:
    """``is_local=True`` but ``db_path=None`` (pre-resolution) renders"""
    text = _format({"total": 0.05, "request_count": 3}, is_local=True, db_path=None)
    assert "(as of" not in text


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
    """``HttpClient.is_connected = False`` (after a ConnectError)"""
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
    """When the stub stops failing, the next poll tick replaces the"""
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
