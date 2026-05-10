"""Sessions tab body (REQ-VG-TUI-002).

Lists recent voice sessions consumed from
:meth:`MetricsClient.list_sessions`. Sortable by cost or time on the
``s`` keybinding; the header line names the active sort with bracket
markers so the indicator is unambiguous in plain ASCII (no Unicode
arrows or emoji per project rules).

Mounted inside :class:`TUIApp`'s :class:`ContentSwitcher` keyed
``sessions``. The base class is :class:`Container` rather than
:class:`textual.screen.Screen` because the four tab panels mount
peer-of-widgets simultaneously; Phase 3's bullet 4 (Enter-to-detail)
will use :meth:`App.push_screen` for the per-turn modal where the
:class:`Screen` API actually applies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.widgets import Label, Static

from voicegateway.cli.tui.screens._focus import FocusRowsMixin
from voicegateway.cli.tui.widgets.session_row import SessionRow

if TYPE_CHECKING:  # pragma: no cover
    # String-only reference at type-check time; the runtime import
    # would close a circular loop with voicegateway.cli.tui.app
    # (which re-exports SessionsScreen via screens/__init__.py).
    from voicegateway.cli.tui.app import TUIApp

#: Sort modes the daemon's ``/v1/sessions`` regex accepts (verified
#: in iteration 6 against ``server.py:339``). LocalClient mirrors the
#: same set since both share storage.list_sessions's order_by param.
_SORT_TIME = "started_at_desc"
_SORT_COST = "cost_desc"


class SessionsScreen(FocusRowsMixin, Container):
    """List of recent sessions with vim-style sort toggle.

    Empty state is the literal string "No sessions yet." per the
    coding-standards rule "every screen handles empty state".
    """

    # No ClassVar annotation: DOMNode declares BINDINGS with a wider
    # union (Binding | tuple) and a narrower list[Binding] override
    # trips mypy invariance. Matching the parent's shape via plain
    # assignment is the canonical Textual pattern (see app.py too).
    BINDINGS = [
        Binding("s", "toggle_sort", "Sort"),
        Binding("enter", "open_detail", "Detail"),
        Binding("j", "focus_next_row", "Down"),
        Binding("k", "focus_prev_row", "Up"),
        Binding("g", "focus_first_row", "First"),
        Binding("G", "focus_last_row", "Last"),
        # Vim users sometimes alias h/l to k/j for vertical lists; we
        # honour the muscle memory but hide the rows from the footer
        # hint so new users see j/k as the documented contract.
        Binding("h", "focus_prev_row", "Up", show=False),
        Binding("l", "focus_next_row", "Down", show=False),
    ]

    def _focusable_rows(self) -> list[SessionRow]:
        return list(self.query_one("#sessions-list", VerticalScroll).query(SessionRow))

    # Layout-only rules; cross-screen treatment (header bar, list
    # scrollbar, empty-state body) lives in main.tcss as
    # ``.tab-header`` / ``.tui-list`` / ``.empty-state`` utilities.
    DEFAULT_CSS = """
    SessionsScreen {
        layout: vertical;
        padding: 1;
    }
    """

    # Per-instance default. Mutating ``self._sort`` in action_toggle_sort
    # shadows this class-level attribute, giving each SessionsScreen
    # its own state without needing a custom __init__ signature.
    _sort: str = _SORT_TIME

    def compose(self) -> ComposeResult:
        yield Label(self._header_text(), id="sessions-header", classes="tab-header")
        yield VerticalScroll(id="sessions-list", classes="tui-list")

    async def on_mount(self) -> None:
        await self.refresh_data()
        # Live-append polling: re-fetch on the client's poll_seconds
        # cadence so a new conversation surfaces at the top within
        # the REQ-VG-TUI-007 5 s budget. set_interval keeps firing
        # while another tab is current (ContentSwitcher hides but
        # does not unmount), so the user finds fresh rows the moment
        # they switch back to Sessions.
        app = cast("TUIApp", self.app)
        poll_seconds = float(getattr(app.client, "poll_seconds", 1.0))
        self.set_interval(poll_seconds, self._poll_tick)

    def _poll_tick(self) -> None:
        """Sync wrapper around refresh_data; matches LogsScreen."""
        self.run_worker(self.refresh_data(), exclusive=True)

    async def refresh_data(self) -> None:
        """Re-fetch the current sort + re-render the list.

        Replaces the list of children rather than diffing because
        v0.1.1's payload is small (typically <100 sessions) and the
        full-rebuild keeps the code simple. Focus is preserved
        across the rebuild by remembering the focused row's
        ``session_id`` and re-focusing the matching row after the
        new children mount; the active session falls back to losing
        focus only when it disappears from the response (which is
        rare and self-explanatory under started_at_desc).
        """
        app = cast("TUIApp", self.app)
        limit = int(getattr(app, "_history_limit", 100))
        sessions = await app.client.list_sessions(limit=limit, order_by=self._sort)

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
        # exclusive=True so a fast double-press cancels the in-flight
        # worker rather than racing two refreshes against each other.
        self.run_worker(self.refresh_data(), exclusive=True)

    def action_open_detail(self) -> None:
        """Push the per-turn detail modal for the focused row.

        Reads ``self.app.focused`` rather than looking up the row in
        the list because focus is the user's intent: ``Enter`` only
        makes sense when a row is the active widget. Falls through
        silently when the focused widget is not a SessionRow (the
        binding does nothing harmful).
        """
        # Lazy import: SessionDetailScreen imports the cli.tui.screens
        # package back through its __init__.py during module init,
        # which would close a loop here. Importing inside the action
        # body sidesteps that without making the call any more
        # expensive than a typical attribute resolution.
        from voicegateway.cli.tui.screens.session_detail import (
            SessionDetailScreen,
        )

        focused = self.app.focused
        if isinstance(focused, SessionRow):
            self.app.push_screen(SessionDetailScreen(focused.session_id))

    # -- Helpers -----------------------------------------------------

    def _header_text(self) -> str:
        """Active-sort indicator. Brackets mark the live mode so the
        plain-ASCII rendering reads unambiguously even on terminals
        that drop styling.
        """
        if self._sort == _SORT_COST:
            return "Sessions  |  Sort:  time   [cost]   (press `s` to toggle)"
        return "Sessions  |  Sort: [time]   cost    (press `s` to toggle)"
