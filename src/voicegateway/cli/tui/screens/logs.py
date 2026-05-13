"""Logs tab body"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Input, Label

from voicegateway.cli.tui.widgets.log_tail import LogTail

if TYPE_CHECKING:  # pragma: no cover
    from voicegateway.cli.tui.app import TUIApp


class LogsScreen(Container):
    """Tail-style request-log viewer with vim-style ``/`` filter."""

    can_focus = True

    BINDINGS = [
        Binding("slash", "open_filter", "Filter"),
        Binding("escape", "clear_filter", "Clear filter", show=False),
        Binding("j", "scroll_down", "Down"),
        Binding("k", "scroll_up", "Up"),
        Binding("g", "scroll_top", "Top"),
        Binding("G", "scroll_bottom", "Bottom"),
        Binding("h", "scroll_up", "Up", show=False),
        Binding("l", "scroll_down", "Down", show=False),
    ]

    DEFAULT_CSS = """
    LogsScreen {
        layout: vertical;
        padding: 1;
    }
    LogsScreen #logs-filter {
        height: 1;
        margin: 0;
    }
    LogsScreen #logs-tail {
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label(self._header_text(), id="logs-header", classes="tab-header")
        yield Input(
            placeholder="Filter (type substring, Enter to apply, Esc to clear)",
            id="logs-filter",
        )
        yield LogTail(id="logs-tail")

    def on_mount(self) -> None:
        self.query_one("#logs-filter", Input).display = False
        self.run_worker(cast(Any, self.refresh_data), exclusive=True)
        app = cast("TUIApp", self.app)
        poll_seconds = float(getattr(app.client, "poll_seconds", 1.0))
        self.set_interval(poll_seconds, self._poll_tick)

    def _poll_tick(self) -> None:
        """Recurring poll callback. Dispatches ``refresh_data``."""
        self.run_worker(cast(Any, self.refresh_data), exclusive=True)

    async def refresh_data(self) -> None:
        """Fetch recent rows and append the unseen ones to LogTail."""
        app = cast("TUIApp", self.app)
        limit = int(getattr(app, "_history_limit", 100))
        try:
            entries = await app.client.list_logs(limit=limit)
        except Exception:  # noqa: BLE001
            return
        self.query_one("#logs-tail", LogTail).append_entries(entries)

    # -- Actions -----------------------------------------------------

    def action_open_filter(self) -> None:
        """Reveal + focus the filter input."""
        filter_input = self.query_one("#logs-filter", Input)
        filter_input.display = True
        filter_input.focus()

    def action_clear_filter(self) -> None:
        """Clear any active filter + hide the input."""
        filter_input = self.query_one("#logs-filter", Input)
        filter_input.value = ""
        filter_input.display = False
        tail = self.query_one("#logs-tail", LogTail)
        tail.set_filter(None)
        self._update_header()
        self.focus()

    # -- Vim scroll actions (REQ-VG-TUI-006) -----------------------

    def action_scroll_down(self) -> None:
        self.query_one("#logs-tail", LogTail).scroll_down(animate=False)

    def action_scroll_up(self) -> None:
        self.query_one("#logs-tail", LogTail).scroll_up(animate=False)

    def action_scroll_top(self) -> None:
        self.query_one("#logs-tail", LogTail).scroll_home(animate=False)

    def action_scroll_bottom(self) -> None:
        self.query_one("#logs-tail", LogTail).scroll_end(animate=False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Apply the substring filter on Enter inside the filter input."""
        if event.input.id != "logs-filter":
            return
        substring = (event.value or "").strip()
        tail = self.query_one("#logs-tail", LogTail)
        tail.set_filter(substring or None)
        event.input.display = False
        self._update_header()
        self.focus()

    def _update_header(self) -> None:
        self.query_one("#logs-header", Label).update(self._header_text())

    def _header_text(self) -> str:
        try:
            tail = self.query_one("#logs-tail", LogTail)
        except Exception:  # noqa: BLE001
            return "Logs  |  [ tailing ]"
        if tail._filter:
            return f"Logs  |  [ filter: {tail._filter} ]   (Esc to clear)"
        return "Logs  |  [ tailing ]"
