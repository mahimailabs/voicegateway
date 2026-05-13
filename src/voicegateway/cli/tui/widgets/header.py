"""Custom ``HeaderBar`` for the v0.1.1 TUI (REQ-VG-TUI-008 chip).

Replaces Textual's built-in :class:`Header` so we can render the
persistent ``[Local mode]`` chip on the right when the App was
launched with ``--local``. Locked decision 5: the chip is the
visible signal that ``--local`` won regardless of whether the
daemon happens to be reachable; without it a stale shell alias
could re-route the user through Local mode silently.

Layout: title on the left (full width), chip on the right (1
character of padding either side). The chip's background is
``$warning`` rather than the active-state ``$accent`` so the user
can never confuse "this is the live tab" with "you are in
Local mode" -- they're different tones in the same column.
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static


class HeaderBar(Horizontal):
    """One-row header with optional ``[Local mode]`` chip on the right."""

    DEFAULT_CSS = """
    HeaderBar {
        height: 1;
        background: $boost;
    }
    HeaderBar #header-title {
        width: 1fr;
        padding: 0 1;
        text-style: bold;
    }
    HeaderBar #header-chip {
        padding: 0 1;
    }
    HeaderBar.-local-mode #header-chip {
        background: $warning;
        color: $background;
        text-style: bold;
    }
    """

    def __init__(self, *, is_local: bool, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._is_local = is_local
        if is_local:
            # Class assignment in __init__ lands before mount so the
            # chip's styled background is applied on first paint
            # rather than flashing the unstyled state.
            self.add_class("-local-mode")

    def compose(self) -> ComposeResult:
        yield Static("VoiceGateway", id="header-title")
        yield Static(self._chip_text(), id="header-chip")

    def _chip_text(self) -> str:
        return "[Local mode]" if self._is_local else ""


__all__ = ["HeaderBar"]
