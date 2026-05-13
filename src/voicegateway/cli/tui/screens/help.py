"""help-overlay modal"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import ContentSwitcher, Static


class HelpOverlay(ModalScreen[None]):
    """Modal cheatsheet of currently-active keybindings."""

    BINDINGS = [
        Binding("question_mark", "dismiss", "Close"),
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
    ]

    DEFAULT_CSS = """
    HelpOverlay {
        align: center middle;
    }
    HelpOverlay > Container {
        width: 60;
        max-height: 30;
        padding: 1 2;
        background: $panel;
        border: thick $accent;
    }
    HelpOverlay #help-title {
        text-style: bold;
        margin-bottom: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("Keybindings", id="help-title")
            yield VerticalScroll(
                Static(self._build_text(), id="help-body"),
                id="help-scroll",
            )

    def _build_text(self) -> str:
        sections: list[tuple[str, list[tuple[str, str]]]] = []
        sections.append(("Global", _bindings_for(self.app)))
        active = self._active_tab_widget()
        if active is not None:
            label = type(active).__name__
            screen_bindings = _bindings_for(active)
            if screen_bindings:
                sections.append((label, screen_bindings))
        sections.append(("Help", _bindings_for(self)))
        return _format_sections(sections)

    def _active_tab_widget(self) -> Any:
        """Walk the screen stack to find the non-modal screen + the
        active tab widget inside it.
        """
        for screen in self.app.screen_stack:
            if isinstance(screen, ModalScreen):
                continue
            try:
                switcher = screen.query_one("#content", ContentSwitcher)
            except Exception:  # noqa: BLE001
                continue
            if switcher.current is None:
                return None
            try:
                return screen.query_one(f"#{switcher.current}")
            except Exception:  # noqa: BLE001
                return None
        return None


def _bindings_for(obj: Any) -> list[tuple[str, str]]:
    """Extract ``(key, description)`` pairs from ``obj.BINDINGS``."""
    raw = getattr(obj, "BINDINGS", None) or []
    pairs: list[tuple[str, str]] = []
    for entry in raw:
        key, desc, show = _entry_fields(entry)
        if not show:
            continue
        pairs.append((_format_key(key), desc))
    return pairs


def _entry_fields(entry: Any) -> tuple[str, str, bool]:
    """Pull ``(key, description, show)`` from a Binding or tuple."""
    if isinstance(entry, Binding):
        return entry.key, entry.description or entry.action, entry.show
    if isinstance(entry, tuple):
        key = str(entry[0]) if entry else ""
        desc = ""
        if len(entry) >= 3:
            desc = str(entry[2])
        elif len(entry) >= 2:
            desc = str(entry[1])
        return key, desc, True
    return "", "", False


def _format_key(key: str) -> str:
    """Render Textual's key vocabulary in human-readable form."""
    aliases = {
        "question_mark": "?",
        "slash": "/",
        "shift+tab": "shift+tab",
    }
    return aliases.get(key, key)


def _format_sections(
    sections: list[tuple[str, list[tuple[str, str]]]],
) -> str:
    """Render the per-section cheatsheet as a single fixed-width block."""
    out: list[str] = []
    for title, pairs in sections:
        if not pairs:
            continue
        out.append(title)
        out.append("-" * len(title))
        for key, desc in pairs:
            out.append(f"  {key:<14}  {desc}")
        out.append("")
    return "\n".join(out).rstrip()


__all__ = ["HelpOverlay"]
