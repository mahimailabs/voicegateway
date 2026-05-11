"""Shared focus-traversal mixin for the v0.1.1 list screens.

Sessions / Logs / Providers all expose a vertical list of focusable
rows; this mixin gives them a single source of truth for the vim
``j/k/g/G`` (and the alias ``h/l``) movement contract from
REQ-VG-TUI-006. Each subclass implements ``_focusable_rows`` to
return its rows in display order; the mixin provides four action
methods the ``BINDINGS`` reference.

Mixin order matters: subclass as ``class X(FocusRowsMixin, Container)``
so the mixin's ``action_*`` lookups happen on the class before
:class:`textual.containers.Container`'s base behaviour.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from textual.app import App
    from textual.widget import Widget


class FocusRowsMixin:
    """Vim ``j/k/g/G`` (+ ``h/l`` aliases) traversal across child rows."""

    # ``Sequence[Widget]`` (covariant) so subclasses can return a
    # narrower ``list[SessionRow]`` / ``list[ProviderRow]`` without
    # tripping mypy's ``list`` invariance check on the override.
    def _focusable_rows(self) -> Sequence[Widget]:
        raise NotImplementedError

    # -- Actions wired by per-screen BINDINGS ------------------------

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

    # -- Helpers -----------------------------------------------------

    def _current_row_index(self, rows: Sequence[Widget]) -> int | None:
        app: App = self.app  # type: ignore[attr-defined]
        focused = app.focused
        if focused in rows:
            return rows.index(focused)
        return None
