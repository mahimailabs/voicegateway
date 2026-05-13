"""Pilot tests for the Phase-3 Sessions + Phase-4 Costs screens."""

from __future__ import annotations

import asyncio
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
from voicegateway.cli.tui.screens.providers import ProvidersScreen
from voicegateway.cli.tui.screens.session_detail import (
    SessionDetailScreen,
    _format_detail,
)
from voicegateway.cli.tui.screens.sessions import SessionsScreen
from voicegateway.cli.tui.widgets.cost_card import CostCard
from voicegateway.cli.tui.widgets.log_tail import LogTail
from voicegateway.cli.tui.widgets.provider_row import ProviderRow
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
    """TUIApp with a seeded LocalClient backing it."""
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


async def _wait_for_session_ids(app: TUIApp, pilot: Any, expected: set[str]) -> None:
    """Wait until the sessions list has mounted the expected row ids."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5.0
    while loop.time() < deadline:
        ids = {row.session_id for row in app.query(SessionRow)}
        if ids == expected:
            return
        await pilot.pause(0.05)
    ids = {row.session_id for row in app.query(SessionRow)}
    assert ids == expected


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
    """Action falls through silently when the focused widget is not"""
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
    """Stub :class:`MetricsClient` with canned per-period responses."""

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
    """TUIApp wired to a deterministic :class:`_StubMetricsClient`."""
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
    """Every refresh requests the per-modality stamps so the"""
    async with costs_app.run_test() as pilot:
        await pilot.press("2")
        await _settle(pilot)
        client = costs_app.client  # type: ignore[attr-defined]
        assert client.calls, "client.list_costs not called"
        assert all(c["include_pricing_source"] for c in client.calls)


async def test_costs_freshness_marker_renders_on_stale_modality(
    costs_app: TUIApp,
) -> None:
    """The Phase-4 freshness indicator surfaces ``(as of YYYY-MM-DD)``"""
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


async def test_costs_screen_polls_for_live_refresh() -> None:
    """``CostsScreen.on_mount`` registers an interval so the card"""
    responses = {"today": {"period": "today", "total": 0.05}}
    client = _StubMetricsClient(responses)
    client.poll_seconds = 0.05  # tight for the test budget
    app = TUIApp(client=client, is_local=True)
    async with app.run_test() as pilot:
        await pilot.press("2")
        await _settle(pilot)

        card = app.query_one(CostsScreen).query_one(CostCard)
        assert card._costs.get("total") == 0.05
        initial_calls = len(client.calls)

        # Simulate a fresh request landing in storage between polls.
        responses["today"]["total"] = 0.42

        # Pump the event loop until the next poll tick fires.
        for _ in range(40):
            await pilot.pause()
            if card._costs.get("total") == 0.42:
                break

        assert card._costs.get("total") == 0.42, (
            "Costs card did not pick up the synthetic change; "
            "CostsScreen.on_mount must register a polling interval."
        )
        assert len(client.calls) > initial_calls


# ---------------------------------------------------------------------------
# Logs screen (REQ-VG-TUI-004)
# ---------------------------------------------------------------------------


class _LogsStubClient:
    """Stub :class:`MetricsClient` driving ``list_logs`` from a"""

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
    """A new entry added after mount surfaces in LogTail within a"""
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


# ---------------------------------------------------------------------------
# Providers screen (REQ-VG-TUI-005)
# ---------------------------------------------------------------------------


class _ProvidersStubClient:
    """Stub :class:`MetricsClient` for the Providers tests."""

    def __init__(self) -> None:
        self.providers: list[dict[str, Any]] = []
        self.test_calls: list[str] = []
        self.test_response: dict[str, Any] | None = {"status": "ok"}
        self.poll_seconds = 1.0

    async def list_providers(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self.providers)

    async def test_provider(self, provider_id: str) -> dict[str, Any]:
        self.test_calls.append(provider_id)
        if self.test_response is None:
            from voicegateway.cli.tui.data.exceptions import (
                LocalModeUnsupportedError,
            )

            raise LocalModeUnsupportedError(feature="test_provider")
        return self.test_response

    # Other Protocol methods are no-ops.
    async def list_sessions(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def get_session_detail(self, session_id: str) -> dict[str, Any] | None:
        return None

    async def list_costs(self, **kwargs: Any) -> dict[str, Any]:
        return {}

    async def list_logs(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []


def _three_provider_fixture() -> list[dict[str, Any]]:
    return [
        {
            "provider_id": "openai-prod",
            "provider_type": "openai",
            "project": "default",
            "status": "ok",
        },
        {
            "provider_id": "deepgram-broken",
            "provider_type": "deepgram",
            "project": "default",
            "status": "fail",
        },
        {
            "provider_id": "cartesia-fresh",
            "provider_type": "cartesia",
            "project": None,
            "status": "untested",
        },
    ]


@pytest.fixture
def providers_app() -> tuple[TUIApp, _ProvidersStubClient]:
    client = _ProvidersStubClient()
    client.providers.extend(_three_provider_fixture())
    return TUIApp(client=client, is_local=False), client


async def test_providers_renders_three_indicator_states(
    providers_app: tuple[TUIApp, _ProvidersStubClient],
) -> None:
    """Three rows; one each of ok / fail / untested. Each row's"""
    app, _ = providers_app
    async with app.run_test() as pilot:
        await pilot.press("4")
        await _settle(pilot)
        rows = list(app.query(ProviderRow))
        assert len(rows) == 3
        statuses = {r.provider_id: r.status for r in rows}
        assert statuses == {
            "openai-prod": "ok",
            "deepgram-broken": "fail",
            "cartesia-fresh": "untested",
        }


async def test_providers_empty_state_renders_static() -> None:
    """An empty client mounts cleanly; the empty-state Static"""
    client = _ProvidersStubClient()  # providers list left empty
    app = TUIApp(client=client, is_local=False)
    async with app.run_test() as pilot:
        await pilot.press("4")
        await _settle(pilot)
        screen = app.query_one(ProvidersScreen)
        rows = list(screen.query(ProviderRow))
        assert rows == []
        empties = [
            s
            for s in screen.query(Static)
            if "No providers configured" in str(s.renderable)
        ]
        assert empties


async def test_providers_test_shortcut_updates_indicator_in_gateway_mode(
    providers_app: tuple[TUIApp, _ProvidersStubClient],
) -> None:
    """``t`` on a focused row drives ``test_provider`` and the"""
    app, client = providers_app
    async with app.run_test() as pilot:
        await pilot.press("4")
        await _settle(pilot)
        rows = list(app.query(ProviderRow))
        target = next(r for r in rows if r.provider_id == "cartesia-fresh")
        assert target.status == "untested"
        target.focus()
        await pilot.pause()
        await pilot.press("t")
        # Worker dispatch + notification path takes a few ticks.
        for _ in range(15):
            await pilot.pause()
        assert client.test_calls == ["cartesia-fresh"]
        assert target.status == "ok"


async def test_providers_test_shortcut_shows_daemon_message_in_local_mode() -> None:
    """In Local mode the stub raises ``LocalModeUnsupportedError``;"""
    client = _ProvidersStubClient()
    client.providers.extend(_three_provider_fixture())
    client.test_response = None  # Trigger LocalModeUnsupportedError.
    app = TUIApp(client=client, is_local=True)
    async with app.run_test() as pilot:
        await pilot.press("4")
        await _settle(pilot)
        rows = list(app.query(ProviderRow))
        target = next(r for r in rows if r.provider_id == "cartesia-fresh")
        assert target.status == "untested"
        target.focus()
        await pilot.pause()
        await pilot.press("t")
        for _ in range(15):
            await pilot.pause()
        # The stub's test_provider was called (so test_calls grew),
        # but it raised LocalModeUnsupportedError so the row's
        # status stays at the documented unchanged value.
        assert client.test_calls == ["cartesia-fresh"]
        assert target.status == "untested"


async def test_providers_test_shortcut_with_no_focus_is_noop(
    providers_app: tuple[TUIApp, _ProvidersStubClient],
) -> None:
    """Action falls through silently when the focused widget is"""
    app, client = providers_app
    async with app.run_test() as pilot:
        await pilot.press("4")
        await _settle(pilot)
        # Don't focus a row; press t at the screen level.
        await pilot.press("t")
        await _settle(pilot)
        assert client.test_calls == []


# ---------------------------------------------------------------------------
# Vim navigation (REQ-VG-TUI-006 movement contract)
# ---------------------------------------------------------------------------


async def test_vim_navigation_works_on_sessions_tab(
    populated_app: TUIApp,
) -> None:
    """``g`` jumps to first row, ``j`` advances, ``G`` jumps to last,"""
    async with populated_app.run_test() as pilot:
        await _settle(pilot)
        rows = list(populated_app.query(SessionRow))
        assert len(rows) == 2
        await pilot.press("g")
        await pilot.pause()
        assert populated_app.focused == rows[0]
        await pilot.press("j")
        await pilot.pause()
        assert populated_app.focused == rows[1]
        await pilot.press("k")
        await pilot.pause()
        assert populated_app.focused == rows[0]
        await pilot.press("G")
        await pilot.pause()
        assert populated_app.focused == rows[-1]


async def test_vim_navigation_works_on_providers_tab(
    providers_app: tuple[TUIApp, _ProvidersStubClient],
) -> None:
    app, _ = providers_app
    async with app.run_test() as pilot:
        await pilot.press("4")
        await _settle(pilot)
        rows = list(app.query(ProviderRow))
        assert len(rows) == 3
        await pilot.press("g")
        await pilot.pause()
        assert app.focused == rows[0]
        await pilot.press("j")
        await pilot.pause()
        assert app.focused == rows[1]
        await pilot.press("G")
        await pilot.pause()
        assert app.focused == rows[2]


async def test_vim_navigation_h_l_aliases_on_list_screens(
    populated_app: TUIApp,
) -> None:
    """``h`` aliases ``k`` (up) and ``l`` aliases ``j`` (down) on"""
    async with populated_app.run_test() as pilot:
        await _settle(pilot)
        rows = list(populated_app.query(SessionRow))
        await pilot.press("g")
        await pilot.pause()
        await pilot.press("l")  # alias of j
        await pilot.pause()
        assert populated_app.focused == rows[1]
        await pilot.press("h")  # alias of k
        await pilot.pause()
        assert populated_app.focused == rows[0]


async def test_vim_navigation_on_logs_tab_dispatches_scroll_actions(
    logs_app: tuple[TUIApp, _LogsStubClient],
) -> None:
    """LogsScreen has no row widgets; ``j``/``k``/``g``/``G`` map to"""
    app, _ = logs_app
    async with app.run_test() as pilot:
        await pilot.press("3")
        await _settle(pilot)
        # Each press should dispatch cleanly without raising.
        for key in ("g", "j", "k", "G", "h", "l"):
            await pilot.press(key)
            await pilot.pause()


# ---------------------------------------------------------------------------
# Sessions live append (REQ-VG-TUI-007)
# ---------------------------------------------------------------------------


class _SessionsStubClient:
    """Stub MetricsClient with a mutable sessions list so tests can"""

    def __init__(self) -> None:
        self.sessions: list[dict[str, Any]] = []
        self.poll_seconds = 0.05

    async def list_sessions(
        self,
        *,
        limit: int = 100,
        project: str | None = None,
        order_by: str = "started_at_desc",
    ) -> list[dict[str, Any]]:
        return list(self.sessions[:limit])

    async def get_session_detail(self, session_id: str) -> dict[str, Any] | None:
        for session in self.sessions:
            if session.get("id") == session_id:
                return session
        return None

    async def list_costs(self, **kwargs: Any) -> dict[str, Any]:
        return {}

    async def list_logs(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def list_providers(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def test_provider(self, provider_id: str) -> dict[str, Any]:
        return {}


async def test_sessions_live_append_picks_up_new_session() -> None:
    """A new session added to the stub mid-Pilot surfaces in the"""
    client = _SessionsStubClient()
    client.sessions.append(
        {
            "id": "vg-old",
            "project": "default",
            "started_at": "2026-05-10T16:00:00+00:00",
            "ended_at": "2026-05-10T16:00:30+00:00",
            "total_cost_usd": 0.001,
            "providers": ["openai"],
            "modalities": ["llm"],
            "request_count": 1,
        }
    )
    app = TUIApp(client=client, is_local=False)
    async with app.run_test() as pilot:
        await _wait_for_session_ids(app, pilot, {"vg-old"})

        # Add a new session; the polling loop should pick it up.
        client.sessions.insert(
            0,
            {
                "id": "vg-new",
                "project": "default",
                "started_at": "2026-05-10T17:00:00+00:00",
                "ended_at": "2026-05-10T17:00:10+00:00",
                "total_cost_usd": 0.002,
                "providers": ["openai"],
                "modalities": ["llm"],
                "request_count": 1,
            },
        )
        await _wait_for_session_ids(app, pilot, {"vg-old", "vg-new"})
