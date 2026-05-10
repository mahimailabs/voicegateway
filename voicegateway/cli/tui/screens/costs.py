"""Costs tab (REQ-VG-TUI-003).

Phase 4 replaces this placeholder body with the real implementation:
today's total + per-modality breakdown consumed from
``self.app.client.list_costs``, with a time-range selector and the
``as of X`` freshness indicator when the genai-prices stamp is
older than 24 h.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static


class CostsScreen(Container):
    """Placeholder body for the Costs tab.

    Mounted inside ``TUIApp``'s ``ContentSwitcher`` keyed ``costs``.
    Phase 4 swaps this body for the real total + breakdown view.
    """

    def compose(self) -> ComposeResult:
        yield Static(
            "Costs placeholder. Phase 4 fills this with the today's-"
            "total + per-modality breakdown.",
            id="costs-placeholder",
        )
