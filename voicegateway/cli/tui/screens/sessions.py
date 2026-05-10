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


class SessionsScreen(Container):
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
    ]

    DEFAULT_CSS = """
    SessionsScreen {
        layout: vertical;
    }
    SessionsScreen #sessions-header {
        height: 1;
        padding: 0 1;
        background: $boost;
    }
    SessionsScreen #sessions-list {
        scrollbar-background: $surface;
    }
    SessionsScreen .empty {
        height: 100%;
        content-align: center middle;
        color: $text-muted;
    }
    """

    # Per-instance default. Mutating ``self._sort`` in action_toggle_sort
    # shadows this class-level attribute, giving each SessionsScreen
    # its own state without needing a custom __init__ signature.
    _sort: str = _SORT_TIME

    def compose(self) -> ComposeResult:
        yield Label(self._header_text(), id="sessions-header")
        yield VerticalScroll(id="sessions-list")

    async def on_mount(self) -> None:
        await self.refresh_data()

    async def refresh_data(self) -> None:
        """Re-fetch the current sort + re-render the list.

        Called from on_mount and whenever the sort toggles. Replaces
        the entire list of children rather than diffing because v0.1.1's
        sort flip is a user-driven action, not a hot-path: simpler is
        better than clever. The Phase-9 polling iteration will
        revisit if focus loss becomes a real complaint.
        """
        app = cast("TUIApp", self.app)
        limit = int(getattr(app, "_history_limit", 100))
        sessions = await app.client.list_sessions(limit=limit, order_by=self._sort)

        list_view = self.query_one("#sessions-list", VerticalScroll)
        await list_view.remove_children()
        if not sessions:
            await list_view.mount(Static("No sessions yet.", classes="empty"))
            return
        for session in sessions:
            await list_view.mount(SessionRow(session))

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
