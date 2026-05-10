"""Sessions tab (REQ-VG-TUI-002).

Phase 3 replaces this placeholder body with the real implementation:
a sortable list of recent voice sessions consumed from
``self.app.client.list_sessions``, with cost/time toggle on ``s``
and per-turn drill-in via Enter (push_screen modal).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static


class SessionsScreen(Container):
    """Placeholder body for the Sessions tab.

    Mounted inside ``TUIApp``'s ``ContentSwitcher`` keyed
    ``sessions``. Phase 3 swaps this body for the real list view.
    """

    def compose(self) -> ComposeResult:
        yield Static(
            "Sessions placeholder. Phase 3 fills this with the sortable session list.",
            id="sessions-placeholder",
        )
