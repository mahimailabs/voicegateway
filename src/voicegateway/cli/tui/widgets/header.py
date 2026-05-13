"""Custom ``HeaderBar`` for the TUI."""

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
            self.add_class("-local-mode")

    def compose(self) -> ComposeResult:
        yield Static("VoiceGateway", id="header-title")
        yield Static(self._chip_text(), id="header-chip")

    def _chip_text(self) -> str:
        return "[Local mode]" if self._is_local else ""


__all__ = ["HeaderBar"]
