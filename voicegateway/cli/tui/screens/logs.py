"""Logs tab body (REQ-VG-TUI-004).

Renders recent request rows in a :class:`LogTail` (a RichLog
subclass with id-based de-dup). The header line carries a
``[ tailing ]`` / ``[ scrolled up ]`` indicator that flips based on
whether the RichLog is currently at the bottom; the next Phase
iterations land the ``/`` filter (Phase 5 bullet 3) and the live-
append polling (Phase 5 bullet 4 + Phase 9 reconnection).

Mounted inside :class:`TUIApp`'s :class:`ContentSwitcher` keyed
``logs``. ``can_focus = True`` so the screen-level BINDINGS (filter
in the next bullet, plus the eventual Phase-7 vim ``j/k/G`` set)
resolve when the screen is the active tab.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Input, Label

from voicegateway.cli.tui.widgets.log_tail import LogTail

if TYPE_CHECKING:  # pragma: no cover
    from voicegateway.cli.tui.app import TUIApp


class LogsScreen(Container):
    """Tail-style request-log viewer with vim-style ``/`` filter."""

    # ``can_focus = True`` for the same reason as CostsScreen: the
    # screen-level BINDINGS (the / filter below; Phase-7 will add
    # j/k/G) only resolve when something in the screen's DOM subtree
    # holds focus. RichLog itself is focusable, but until the initial
    # fetch completes the LogTail may be empty + un-focused, so the
    # screen Container is the safe focus target.
    can_focus = True

    BINDINGS = [
        Binding("slash", "open_filter", "Filter"),
        Binding("escape", "clear_filter", "Clear filter", show=False),
    ]

    DEFAULT_CSS = """
    LogsScreen {
        layout: vertical;
        padding: 1;
    }
    LogsScreen #logs-header {
        height: 1;
        padding: 0 1;
        background: $boost;
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
        yield Label(self._header_text(), id="logs-header")
        yield Input(
            placeholder="Filter (type substring, Enter to apply, Esc to clear)",
            id="logs-filter",
        )
        yield LogTail(id="logs-tail")

    def on_mount(self) -> None:
        # Filter input is hidden by default; ``/`` reveals it.
        self.query_one("#logs-filter", Input).display = False
        # Initial fetch as a worker so on_mount stays sync.
        self.run_worker(self.refresh_data(), exclusive=True)
        # Live-append polling: re-fetch on the client's ``poll_seconds``
        # cadence (1.0 s in Gateway mode, 5.0 s in Local mode by
        # default; ``--poll`` on the Typer command overrides). LogTail
        # de-dups by id so the overlapping fetch windows produce a
        # forward-only visible tail. set_interval keeps firing even
        # while another tab is current (ContentSwitcher hides but does
        # not unmount), which is what we want -- new rows show up in
        # the Logs tab the moment the user switches back.
        app = cast("TUIApp", self.app)
        poll_seconds = float(getattr(app.client, "poll_seconds", 1.0))
        self.set_interval(poll_seconds, self._poll_tick)

    def _poll_tick(self) -> None:
        """Recurring poll callback. Dispatches ``refresh_data``.

        Sync wrapper around the async fetch because Textual's
        ``set_interval`` accepts only sync callbacks. The worker is
        ``exclusive=True`` so a slow daemon never piles up parallel
        refreshes; a delayed response is dropped in favour of the
        next poll's fresh request.
        """
        self.run_worker(self.refresh_data(), exclusive=True)

    async def refresh_data(self) -> None:
        """Fetch recent rows and append the unseen ones to LogTail.

        The LogTail's de-dup set drops rows whose ``id`` we have
        already rendered, so this method is safe to call repeatedly
        from the polling loop. With a filter active, non-matching
        rows still land in ``LogTail._all_entries`` so a later
        clear restores the full view.
        """
        app = cast("TUIApp", self.app)
        limit = int(getattr(app, "_history_limit", 100))
        entries = await app.client.list_logs(limit=limit)
        self.query_one("#logs-tail", LogTail).append_entries(entries)

    # -- Actions -----------------------------------------------------

    def action_open_filter(self) -> None:
        """Reveal + focus the filter input.

        Bound to ``/`` to match vim's search-prompt convention. The
        user types a substring and presses Enter to apply; Escape
        (handled by ``action_clear_filter``) clears + hides.
        """
        filter_input = self.query_one("#logs-filter", Input)
        filter_input.display = True
        filter_input.focus()

    def action_clear_filter(self) -> None:
        """Clear any active filter + hide the input.

        Triggered by Escape regardless of whether the input has
        focus (the screen-level binding wins when no Input handler
        consumes the key).
        """
        filter_input = self.query_one("#logs-filter", Input)
        filter_input.value = ""
        filter_input.display = False
        tail = self.query_one("#logs-tail", LogTail)
        tail.set_filter(None)
        self._update_header()
        # Restore focus to the screen so subsequent keys (e.g. tab
        # cycle) are not swallowed by the now-hidden Input.
        self.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Apply the substring filter on Enter inside the filter input."""
        if event.input.id != "logs-filter":
            return
        substring = (event.value or "").strip()
        tail = self.query_one("#logs-tail", LogTail)
        tail.set_filter(substring or None)
        # Hide the input and shift focus back to the screen so the
        # screen-level keybindings (and the upcoming Phase-7
        # j/k/G set) resolve cleanly without an extra step.
        event.input.display = False
        self._update_header()
        self.focus()

    # -- Helpers -----------------------------------------------------

    def _update_header(self) -> None:
        self.query_one("#logs-header", Label).update(self._header_text())

    def _header_text(self) -> str:
        try:
            tail = self.query_one("#logs-tail", LogTail)
        except Exception:  # noqa: BLE001
            # ``compose()`` calls this before LogTail mounts; render
            # the default until on_mount finishes.
            return "Logs  |  [ tailing ]"
        if tail._filter:
            return f"Logs  |  [ filter: {tail._filter} ]   (Esc to clear)"
        return "Logs  |  [ tailing ]"
