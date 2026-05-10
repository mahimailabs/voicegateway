"""Logs tab (REQ-VG-TUI-004).

Phase 5 replaces this placeholder body with the real implementation:
a tail-style RichLog widget consumed from
``self.app.client.list_logs``, with auto-scroll, manual-scroll
detection, ``/`` filter, and live append.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static


class LogsScreen(Container):
    """Placeholder body for the Logs tab.

    Mounted inside ``TUIApp``'s ``ContentSwitcher`` keyed ``logs``.
    Phase 5 swaps this body for the real RichLog tail view.
    """

    def compose(self) -> ComposeResult:
        yield Static(
            "Logs placeholder. Phase 5 fills this with the tail-style "
            "RichLog and the / filter.",
            id="logs-placeholder",
        )
