"""Providers tab (REQ-VG-TUI-005).

Phase 6 replaces this placeholder body with the real implementation:
a list of configured providers with green/red indicators, the ``t``
test shortcut (Gateway mode only), and a clear "requires daemon"
hint for Local mode that surfaces ``LocalModeUnsupportedError``
without silent failure.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static


class ProvidersScreen(Container):
    """Placeholder body for the Providers tab.

    Mounted inside ``TUIApp``'s ``ContentSwitcher`` keyed
    ``providers``. Phase 6 swaps this body for the real list view.
    """

    def compose(self) -> ComposeResult:
        yield Static(
            "Providers placeholder. Phase 6 fills this with the "
            "list + indicator + test shortcut.",
            id="providers-placeholder",
        )
