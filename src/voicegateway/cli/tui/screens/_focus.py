"""Shared focus-traversal mixin for the list screens."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from textual.app import App
    from textual.widget import Widget


class FocusRowsMixin:
    """Vim ``j/k/g/G`` (+ ``h/l`` aliases) traversal across child rows."""

    def _focusable_rows(self) -> Sequence[Widget]:
        raise NotImplementedError

    def action_focus_next_row(self) -> None:
        rows = self._focusable_rows()
        if not rows:
            return
        idx = self._current_row_index(rows)
        target = rows[0] if idx is None else rows[(idx + 1) % len(rows)]
        target.focus()

    def action_focus_prev_row(self) -> None:
        rows = self._focusable_rows()
        if not rows:
            return
        idx = self._current_row_index(rows)
        target = rows[-1] if idx is None else rows[(idx - 1) % len(rows)]
        target.focus()

    def action_focus_first_row(self) -> None:
        rows = self._focusable_rows()
        if rows:
            rows[0].focus()

    def action_focus_last_row(self) -> None:
        rows = self._focusable_rows()
        if rows:
            rows[-1].focus()

    def _current_row_index(self, rows: Sequence[Widget]) -> int | None:
        app: App = self.app  # type: ignore[attr-defined]
        focused = app.focused
        if focused in rows:
            return rows.index(focused)
        return None
