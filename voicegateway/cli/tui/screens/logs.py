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
from textual.containers import Container
from textual.widgets import Label

from voicegateway.cli.tui.widgets.log_tail import LogTail

if TYPE_CHECKING:  # pragma: no cover
    from voicegateway.cli.tui.app import TUIApp


class LogsScreen(Container):
    """Tail-style request-log viewer."""

    # ``can_focus = True`` for the same reason as CostsScreen: the
    # screen-level BINDINGS (added in the next Phase-5 bullets) only
    # resolve when something in the screen's DOM subtree holds focus,
    # and LogTail is RichLog which is focusable -- but until the
    # initial fetch completes the LogTail may be empty + un-focused,
    # so the screen Container is the safe focus target.
    can_focus = True

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
    LogsScreen #logs-tail {
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("Logs  |  [ tailing ]", id="logs-header")
        yield LogTail(id="logs-tail")

    async def on_mount(self) -> None:
        await self.refresh_data()

    async def refresh_data(self) -> None:
        """Fetch recent rows and append the unseen ones to LogTail.

        The LogTail's de-dup set drops rows whose ``id`` we have
        already rendered, so this method is safe to call repeatedly
        from the polling loop the Phase-9 iteration adds.
        """
        app = cast("TUIApp", self.app)
        limit = int(getattr(app, "_history_limit", 100))
        entries = await app.client.list_logs(limit=limit)
        self.query_one("#logs-tail", LogTail).append_entries(entries)
