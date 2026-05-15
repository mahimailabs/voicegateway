"""Sessions tab body"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.widgets import Label, Static

from voicegateway.cli.tui.screens.focus_helpers import FocusRowsMixin
from voicegateway.cli.tui.widgets.session_row import SessionRow

if TYPE_CHECKING:  # pragma: no cover
    from voicegateway.cli.tui.app import TUIApp


_SORT_TIME = "started_at_desc"
_SORT_COST = "cost_desc"


class SessionsScreen(FocusRowsMixin, Container):
    """List of recent sessions with vim-style sort toggle."""

    BINDINGS = [
        Binding("s", "toggle_sort", "Sort"),
        Binding("enter", "open_detail", "Detail"),
        Binding("j", "focus_next_row", "Down"),
        Binding("k", "focus_prev_row", "Up"),
        Binding("g", "focus_first_row", "First"),
        Binding("G", "focus_last_row", "Last"),
        Binding("h", "focus_prev_row", "Up", show=False),
        Binding("l", "focus_next_row", "Down", show=False),
    ]

    def _focusable_rows(self) -> list[SessionRow]:
        return list(self.query_one("#sessions-list", VerticalScroll).query(SessionRow))

    DEFAULT_CSS = """
    SessionsScreen {
        layout: vertical;
        padding: 1;
    }
    """

    _sort: str = _SORT_TIME

    def compose(self) -> ComposeResult:
        yield Label(self._header_text(), id="sessions-header", classes="tab-header")
        yield VerticalScroll(id="sessions-list", classes="tui-list")

    async def on_mount(self) -> None:
        await self.refresh_data()
        app = cast("TUIApp", self.app)
        poll_seconds = float(getattr(app.client, "poll_seconds", 1.0))
        self.set_interval(poll_seconds, self._poll_tick)

    def _poll_tick(self) -> None:
        """Sync wrapper around refresh_data; matches LogsScreen."""
        self.run_worker(cast(Any, self.refresh_data), exclusive=True)

    async def refresh_data(self) -> None:
        """Re-fetch the current sort + re-render the list."""
        app = cast("TUIApp", self.app)
        limit = int(getattr(app, "_history_limit", 100))
        try:
            sessions = await app.client.list_sessions(limit=limit, order_by=self._sort)
        except Exception:  # noqa: BLE001
            return

        list_view = self.query_one("#sessions-list", VerticalScroll)
        focused_id = self._focused_session_id()
        await list_view.remove_children()
        if not sessions:
            await list_view.mount(Static("No sessions yet.", classes="empty-state"))
            return
        for session in sessions:
            await list_view.mount(SessionRow(session))
        if focused_id:
            for row in list_view.query(SessionRow):
                if row.session_id == focused_id:
                    row.focus()
                    break

    def _focused_session_id(self) -> str | None:
        """Storage-canonical id of the currently-focused row, if any."""
        focused = self.app.focused
        if isinstance(focused, SessionRow):
            return focused.session_id
        return None

    # -- Actions -----------------------------------------------------

    def action_toggle_sort(self) -> None:
        """Flip between time-sort and cost-sort, refresh the list."""
        self._sort = _SORT_COST if self._sort == _SORT_TIME else _SORT_TIME
        header = self.query_one("#sessions-header", Label)
        header.update(self._header_text())

        self.run_worker(cast(Any, self.refresh_data), exclusive=True)

    def action_open_detail(self) -> None:
        """Push the per-turn detail modal for the focused row."""

        from voicegateway.cli.tui.screens.session_detail_screen import (
            SessionDetailScreen,
        )

        focused = self.app.focused
        if isinstance(focused, SessionRow):
            self.app.push_screen(SessionDetailScreen(focused.session_id))

    # -- Helpers -----------------------------------------------------

    def _header_text(self) -> str:
        """Active-sort indicator. Brackets mark the live mode so the"""
        if self._sort == _SORT_COST:
            return "Sessions  |  Sort:  time   [cost]   (press `s` to toggle)"
        return "Sessions  |  Sort: [time]   cost    (press `s` to toggle)"
