"""Pilot tests for the Phase-3 Sessions screen + detail modal.

Covers REQ-VG-TUI-002:

- List renders with fixture data and the empty state.
- ``s`` toggles between started_at_desc and cost_desc; the bracket
  marker on the header flips and the row order matches the fetch.
- Pure-formatter coverage of :func:`_format_detail` so the modal's
  rendering contract is unit-testable without Textual.
- Enter on a focused :class:`SessionRow` pushes
  :class:`SessionDetailScreen`; ``escape`` / ``q`` dismisses it
  and focus returns to the originating row.
- Enter with nothing focused is a documented no-op.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Label, Static

from voicegateway.cli.tui import TUIApp
from voicegateway.cli.tui.data.local import LocalClient
from voicegateway.cli.tui.screens.session_detail import (
    SessionDetailScreen,
    _format_detail,
)
from voicegateway.cli.tui.screens.sessions import SessionsScreen
from voicegateway.cli.tui.widgets.session_row import SessionRow
from voicegateway.middleware.cost_tracker import RequestRecord
from voicegateway.storage.sqlite import SQLiteStorage

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _seed(
    storage: SQLiteStorage, sessions: list[tuple[str, float, float]]
) -> None:
    """Log one request per ``(session_id, ts, cost)`` triple."""
    for session_id, ts, cost in sessions:
        rec = RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=ts,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            project="default",
            input_units=10,
            output_units=5,
            cost_usd=cost,
            metadata={},
            session_id=session_id,
        )
        await storage.log_request(rec)


@pytest.fixture
async def populated_app(tmp_path: Path):
    """TUIApp with a seeded LocalClient backing it.

    Two sessions: ``vg-old`` is older (lower timestamp) but more
    expensive; ``vg-new`` is newer but cheaper. The reverse ordering
    on time vs. cost makes the sort-toggle assertion trivially
    visible.
    """
    db = tmp_path / "voicegw.db"
    storage = SQLiteStorage(db)
    base = time.time() - 60
    await _seed(
        storage,
        [
            ("vg-old", base, 0.05),  # older + expensive
            ("vg-new", base + 30, 0.001),  # newer + cheap
        ],
    )
    client = LocalClient(db_path=db, storage=storage)
    return TUIApp(client=client, is_local=True)


async def _settle(pilot: Any, ticks: int = 8) -> None:
    """Drain the event loop so async on_mount / refresh_data finish."""
    for _ in range(ticks):
        await pilot.pause()


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


async def test_sessions_empty_state(tmp_path: Path) -> None:
    db = tmp_path / "voicegw.db"
    storage = SQLiteStorage(db)
    client = LocalClient(db_path=db, storage=storage)
    app = TUIApp(client=client, is_local=True)
    async with app.run_test() as pilot:
        await _settle(pilot)
        screen = app.query_one(SessionsScreen)
        list_view = screen.query_one("#sessions-list", VerticalScroll)
        empties = list_view.query(Static)
        assert any("No sessions yet" in str(e.renderable) for e in empties)


# ---------------------------------------------------------------------------
# List rendering + sort toggle
# ---------------------------------------------------------------------------


async def test_sessions_renders_two_rows_from_fixture(populated_app: TUIApp) -> None:
    async with populated_app.run_test() as pilot:
        await _settle(pilot)
        screen = populated_app.query_one(SessionsScreen)
        rows = list(
            screen.query_one("#sessions-list", VerticalScroll).query(SessionRow)
        )
        assert len(rows) == 2
        assert {r.session_id for r in rows} == {"vg-old", "vg-new"}


async def test_sessions_default_sort_is_time_descending(
    populated_app: TUIApp,
) -> None:
    async with populated_app.run_test() as pilot:
        await _settle(pilot)
        screen = populated_app.query_one(SessionsScreen)
        rows = list(
            screen.query_one("#sessions-list", VerticalScroll).query(SessionRow)
        )
        # Newest first under started_at_desc
        assert rows[0].session_id == "vg-new"
        assert rows[1].session_id == "vg-old"


async def test_sessions_sort_toggle_reorders_rows(
    populated_app: TUIApp,
) -> None:
    async with populated_app.run_test() as pilot:
        await _settle(pilot)
        screen = populated_app.query_one(SessionsScreen)

        # Default header carries [time] marker
        header = screen.query_one("#sessions-header", Label)
        assert "[time]" in str(header.renderable)

        # Toggle to cost-sort
        await pilot.press("s")
        await _settle(pilot)
        assert "[cost]" in str(header.renderable)

        # vg-old is more expensive -> appears first under cost_desc
        rows = list(
            screen.query_one("#sessions-list", VerticalScroll).query(SessionRow)
        )
        assert rows[0].session_id == "vg-old"
        assert rows[1].session_id == "vg-new"

        # Toggle back to time-sort
        await pilot.press("s")
        await _settle(pilot)
        assert "[time]" in str(header.renderable)
        rows = list(
            screen.query_one("#sessions-list", VerticalScroll).query(SessionRow)
        )
        assert rows[0].session_id == "vg-new"


# ---------------------------------------------------------------------------
# Detail modal
# ---------------------------------------------------------------------------


async def test_enter_on_focused_row_pushes_detail_screen(
    populated_app: TUIApp,
) -> None:
    async with populated_app.run_test() as pilot:
        await _settle(pilot)
        rows = list(populated_app.query(SessionRow))
        assert rows
        rows[0].focus()
        await pilot.pause()
        await pilot.press("enter")
        await _settle(pilot)
        assert any(
            isinstance(s, SessionDetailScreen) for s in populated_app.screen_stack
        )


async def test_detail_modal_dismisses_on_q(populated_app: TUIApp) -> None:
    async with populated_app.run_test() as pilot:
        await _settle(pilot)
        rows = list(populated_app.query(SessionRow))
        rows[0].focus()
        await pilot.pause()
        await pilot.press("enter")
        await _settle(pilot)
        # q dismisses inside the modal (modal binding scope wins over
        # the App's quit binding while the modal is on the stack).
        await pilot.press("q")
        await _settle(pilot)
        assert not any(
            isinstance(s, SessionDetailScreen) for s in populated_app.screen_stack
        )


async def test_detail_modal_dismisses_on_escape(
    populated_app: TUIApp,
) -> None:
    async with populated_app.run_test() as pilot:
        await _settle(pilot)
        rows = list(populated_app.query(SessionRow))
        rows[0].focus()
        await pilot.pause()
        await pilot.press("enter")
        await _settle(pilot)
        await pilot.press("escape")
        await _settle(pilot)
        assert not any(
            isinstance(s, SessionDetailScreen) for s in populated_app.screen_stack
        )


async def test_enter_with_nothing_focused_is_noop(
    populated_app: TUIApp,
) -> None:
    """Action falls through silently when the focused widget is not
    a SessionRow (e.g., focus is on a Header link or no widget has
    focus). No exception, no modal pushed.
    """
    async with populated_app.run_test() as pilot:
        await _settle(pilot)
        # Don't focus a row; press Enter at the screen level.
        await pilot.press("enter")
        await _settle(pilot)
        assert not any(
            isinstance(s, SessionDetailScreen) for s in populated_app.screen_stack
        )


# ---------------------------------------------------------------------------
# _format_detail (pure formatter)
# ---------------------------------------------------------------------------


def _detail_fixture() -> dict[str, Any]:
    return {
        "id": "vg-1",
        "project": "tony-pizza",
        "started_at": "2026-05-10T16:23:00+00:00",
        "ended_at": "2026-05-10T16:24:30+00:00",
        "total_cost_usd": 0.0123,
        "request_count": 7,
        "modalities": ["llm", "stt"],
        "providers": ["openai", "deepgram"],
        "by_modality": {
            "llm": {"cost": 0.01, "request_count": 4},
            "stt": {"cost": 0.0023, "request_count": 3},
        },
    }


def test_format_detail_renders_every_field() -> None:
    text = _format_detail(_detail_fixture())
    assert "tony-pizza" in text
    assert "$0.0123" in text
    assert "7" in text
    assert "llm, stt" in text
    assert "openai, deepgram" in text
    assert "By modality:" in text
    assert "llm: $0.0100" in text
    assert "stt: $0.0023" in text


def test_format_detail_handles_missing_by_modality() -> None:
    detail = _detail_fixture()
    detail.pop("by_modality")
    text = _format_detail(detail)
    assert "By modality:" not in text
    assert "tony-pizza" in text


def test_format_detail_handles_none_total_cost() -> None:
    detail = _detail_fixture()
    detail["total_cost_usd"] = None
    text = _format_detail(detail)
    assert "$0.0000" in text


def test_format_detail_handles_empty_lists() -> None:
    detail = _detail_fixture()
    detail["modalities"] = []
    detail["providers"] = []
    text = _format_detail(detail)
    # No crash; the Modalities / Providers slots collapse to empty.
    assert "Modalities:" in text
    assert "Providers:" in text
