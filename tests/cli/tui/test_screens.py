"""Pilot tests for the Phase-3 Sessions + Phase-4 Costs screens.

REQ-VG-TUI-002 (Sessions):

- List renders with fixture data and the empty state.
- ``s`` toggles between started_at_desc and cost_desc; the bracket
  marker on the header flips and the row order matches the fetch.
- Pure-formatter coverage of :func:`_format_detail` so the modal's
  rendering contract is unit-testable without Textual.
- Enter on a focused :class:`SessionRow` pushes
  :class:`SessionDetailScreen`; ``escape`` / ``q`` dismisses it
  and focus returns to the originating row.
- Enter with nothing focused is a documented no-op.

REQ-VG-TUI-003 (Costs):

- Total + per-modality breakdown render from canned client data.
- ``r`` cycles ``today`` / ``this_week`` / ``this_month`` and
  drives a refresh against the active range with wrap-around.
- ``include_pricing_source=True`` reaches the client on every
  refresh so the freshness suffix can render.
- Stale-stamp modalities surface the ``(as of YYYY-MM-DD)`` marker;
  fresh and version-token modalities stay un-marked.
"""

from __future__ import annotations

import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Label, Static

from voicegateway.cli.tui import TUIApp
from voicegateway.cli.tui.data.local import LocalClient
from voicegateway.cli.tui.screens.costs import CostsScreen
from voicegateway.cli.tui.screens.logs import LogsScreen
from voicegateway.cli.tui.screens.session_detail import (
    SessionDetailScreen,
    _format_detail,
)
from voicegateway.cli.tui.screens.sessions import SessionsScreen
from voicegateway.cli.tui.widgets.cost_card import CostCard
from voicegateway.cli.tui.widgets.log_tail import LogTail
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


# ---------------------------------------------------------------------------
# Costs screen (REQ-VG-TUI-003)
# ---------------------------------------------------------------------------


class _StubMetricsClient:
    """Stub :class:`MetricsClient` with canned per-period responses.

    Lets the Costs tests inject ``pricing_sources`` shapes the local
    SQLite path would not produce on its own (specifically a stamp
    older than 24 h for the freshness assertion). Records every
    ``list_costs`` call so tests can verify the active period and
    the ``include_pricing_source`` flag both flow through the
    refresh path.
    """

    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []
        self.poll_seconds = 5.0

    async def list_sessions(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def get_session_detail(self, session_id: str) -> dict[str, Any] | None:
        return None

    async def list_costs(
        self,
        *,
        period: str = "today",
        project: str | None = None,
        include_pricing_source: bool = False,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "period": period,
                "include_pricing_source": include_pricing_source,
                "project": project,
            }
        )
        return self.responses.get(period, {})

    async def list_logs(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def list_providers(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def test_provider(self, provider_id: str) -> dict[str, Any]:
        return {}


@pytest.fixture
def costs_app() -> TUIApp:
    """TUIApp wired to a deterministic :class:`_StubMetricsClient`.

    ``today``: total $0.05, STT stamp old (2025-01-01 -> stale),
    LLM stamp is a SemVer (no age inferable), TTS stamp today.
    ``this_week``: total $0.20, breakdown only.
    ``this_month``: total $1.25, no breakdown (covers fresh-install).
    """
    today = date.today().isoformat()
    responses = {
        "today": {
            "period": "today",
            "total": 0.05,
            "by_modality": {
                "stt": {"cost": 0.01, "request_count": 4},
                "llm": {"cost": 0.04, "request_count": 7},
            },
            "pricing_sources": {
                "stt": "voicegateway-catalog@2025-01-01",
                "llm": "genai-prices@0.0.57",
                "tts": f"voicegateway-catalog@{today}",
            },
        },
        "this_week": {
            "period": "this_week",
            "total": 0.20,
            "by_modality": {
                "stt": {"cost": 0.05, "request_count": 12},
            },
        },
        "this_month": {
            "period": "this_month",
            "total": 1.25,
            "by_modality": {},
        },
    }
    client = _StubMetricsClient(responses)
    return TUIApp(client=client, is_local=True)


async def test_costs_total_renders_from_client(costs_app: TUIApp) -> None:
    async with costs_app.run_test() as pilot:
        await pilot.press("2")
        await _settle(pilot)
        screen = costs_app.query_one(CostsScreen)
        card = screen.query_one(CostCard)
        assert card._costs.get("total") == 0.05
        assert card._costs.get("period") == "today"


async def test_costs_default_header_carries_today_marker(
    costs_app: TUIApp,
) -> None:
    async with costs_app.run_test() as pilot:
        await pilot.press("2")
        await _settle(pilot)
        header = costs_app.query_one(CostsScreen).query_one("#costs-header", Label)
        assert "[today]" in str(header.renderable)


async def test_costs_range_cycle_drives_refresh(costs_app: TUIApp) -> None:
    async with costs_app.run_test() as pilot:
        await pilot.press("2")
        await _settle(pilot)
        client = costs_app.client  # type: ignore[attr-defined]

        # Initial fetch hit `today`.
        assert any(c["period"] == "today" for c in client.calls)

        # Cycle to this_week.
        await pilot.press("r")
        await _settle(pilot)
        assert any(c["period"] == "this_week" for c in client.calls)
        screen = costs_app.query_one(CostsScreen)
        card = screen.query_one(CostCard)
        assert card._costs.get("total") == 0.20

        # Cycle to this_month.
        await pilot.press("r")
        await _settle(pilot)
        assert any(c["period"] == "this_month" for c in client.calls)
        assert card._costs.get("total") == 1.25

        # Wrap back to today.
        await pilot.press("r")
        await _settle(pilot)
        assert card._costs.get("total") == 0.05


async def test_costs_passes_include_pricing_source_true(
    costs_app: TUIApp,
) -> None:
    """Every refresh requests the per-modality stamps so the
    freshness suffix can render.
    """
    async with costs_app.run_test() as pilot:
        await pilot.press("2")
        await _settle(pilot)
        client = costs_app.client  # type: ignore[attr-defined]
        assert client.calls, "client.list_costs not called"
        assert all(c["include_pricing_source"] for c in client.calls)


async def test_costs_freshness_marker_renders_on_stale_modality(
    costs_app: TUIApp,
) -> None:
    """The Phase-4 freshness indicator surfaces ``(as of YYYY-MM-DD)``
    on a stale STT stamp; LLM (SemVer token) and TTS (today) stay
    un-marked. Pins REQ-VG-TUI-003's freshness contract end-to-end.
    """
    async with costs_app.run_test() as pilot:
        await pilot.press("2")
        await _settle(pilot)
        screen = costs_app.query_one(CostsScreen)
        stt = screen.query_one("#cost-modality-stt", Static)
        llm = screen.query_one("#cost-modality-llm", Static)
        tts = screen.query_one("#cost-modality-tts", Static)
        assert "as of 2025-01-01" in str(stt.renderable)
        assert "as of" not in str(llm.renderable)
        assert "as of" not in str(tts.renderable)


# ---------------------------------------------------------------------------
# Logs screen (REQ-VG-TUI-004)
# ---------------------------------------------------------------------------


class _LogsStubClient:
    """Stub :class:`MetricsClient` driving ``list_logs`` from a
    mutable list so tests can simulate live append by appending to
    ``self.entries`` between Pilot pauses. ``poll_seconds`` is short
    so the live-append assertion runs in well under a wall-clock
    second.
    """

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self.poll_seconds = 0.05
        self.calls: list[dict[str, Any]] = []

    async def list_logs(
        self,
        *,
        limit: int = 100,
        project: str | None = None,
        modality: str | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append({"limit": limit, "project": project, "modality": modality})
        # Snapshot so a concurrent append does not mutate the
        # response mid-iteration on the screen side.
        return list(self.entries)

    # Other Protocol methods are no-ops.
    async def list_sessions(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def get_session_detail(self, session_id: str) -> dict[str, Any] | None:
        return None

    async def list_costs(self, **kwargs: Any) -> dict[str, Any]:
        return {}

    async def list_providers(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def test_provider(self, provider_id: str) -> dict[str, Any]:
        return {}


def _log_entry(
    entry_id: str,
    *,
    provider: str = "openai",
    modality: str = "llm",
) -> dict[str, Any]:
    return {
        "id": entry_id,
        "timestamp": time.time(),
        "modality": modality,
        "provider": provider,
        "model_id": f"{provider}/m",
        "project": "default",
        "cost_usd": 0.001,
        "status": "success",
        "total_latency_ms": 100,
    }


@pytest.fixture
def logs_app() -> tuple[TUIApp, _LogsStubClient]:
    client = _LogsStubClient()
    client.entries.extend(
        [
            _log_entry("r1", provider="openai"),
            _log_entry("r2", provider="deepgram", modality="stt"),
            _log_entry("r3", provider="cartesia", modality="tts"),
        ]
    )
    return TUIApp(client=client, is_local=True), client


async def test_logs_renders_seeded_entries(
    logs_app: tuple[TUIApp, _LogsStubClient],
) -> None:
    app, _ = logs_app
    async with app.run_test() as pilot:
        await pilot.press("3")
        await _settle(pilot)
        screen = app.query_one(LogsScreen)
        tail = screen.query_one("#logs-tail", LogTail)
        assert len(tail._all_entries) == 3
        assert {e["id"] for e in tail._all_entries} == {"r1", "r2", "r3"}


async def test_logs_empty_state_does_not_crash() -> None:
    """An empty client mounts cleanly; no rows in LogTail."""
    client = _LogsStubClient()  # entries left empty
    app = TUIApp(client=client, is_local=True)
    async with app.run_test() as pilot:
        await pilot.press("3")
        await _settle(pilot)
        screen = app.query_one(LogsScreen)
        tail = screen.query_one("#logs-tail", LogTail)
        assert tail._all_entries == []


async def test_logs_filter_narrows_visible_via_slash(
    logs_app: tuple[TUIApp, _LogsStubClient],
) -> None:
    app, _ = logs_app
    async with app.run_test() as pilot:
        await pilot.press("3")
        await _settle(pilot)
        screen = app.query_one(LogsScreen)
        tail = screen.query_one("#logs-tail", LogTail)
        await pilot.press("slash")
        await _settle(pilot)
        for ch in "deepgram":
            await pilot.press(ch)
        await pilot.press("enter")
        await _settle(pilot)
        assert tail._filter == "deepgram"
        # All three entries still retained; only display narrowed.
        assert len(tail._all_entries) == 3
        # Filter input hidden after submit.
        from textual.widgets import Input as _Input

        filter_input = screen.query_one("#logs-filter", _Input)
        assert filter_input.display is False
        # Header reflects filter mode.
        header = screen.query_one("#logs-header", Label)
        assert "filter: deepgram" in str(header.renderable)


async def test_logs_filter_clears_on_escape(
    logs_app: tuple[TUIApp, _LogsStubClient],
) -> None:
    app, _ = logs_app
    async with app.run_test() as pilot:
        await pilot.press("3")
        await _settle(pilot)
        screen = app.query_one(LogsScreen)
        tail = screen.query_one("#logs-tail", LogTail)
        await pilot.press("slash")
        await _settle(pilot)
        for ch in "openai":
            await pilot.press(ch)
        await pilot.press("enter")
        await _settle(pilot)
        assert tail._filter == "openai"
        await pilot.press("escape")
        await _settle(pilot)
        assert tail._filter is None
        header = screen.query_one("#logs-header", Label)
        assert "tailing" in str(header.renderable)


async def test_logs_polling_picks_up_new_entries(
    logs_app: tuple[TUIApp, _LogsStubClient],
) -> None:
    """A new entry added after mount surfaces in LogTail within a
    handful of poll ticks (poll_seconds=0.05 in the stub).
    Synthetic-data-change-within-5s contract from REQ-VG-TUI-007's
    Phase-5 half.
    """
    app, client = logs_app
    async with app.run_test() as pilot:
        await pilot.press("3")
        await _settle(pilot)
        screen = app.query_one(LogsScreen)
        tail = screen.query_one("#logs-tail", LogTail)
        before = len(tail._all_entries)
        client.entries.append(_log_entry("r-new", provider="anthropic"))
        # Wait several poll intervals; 0.05s * 40 = 2s, well under
        # the 5s contract budget.
        for _ in range(40):
            await pilot.pause(0.05)
        assert len(tail._all_entries) == before + 1
        assert any(e.get("id") == "r-new" for e in tail._all_entries)
