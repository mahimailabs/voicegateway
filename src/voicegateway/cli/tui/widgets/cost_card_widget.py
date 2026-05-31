"""``CostCard`` widget for the Costs tab"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Container
from textual.css.query import NoMatches
from textual.widgets import Static

_MODALITIES: tuple[str, ...] = ("stt", "llm", "tts")


class CostCard(Container):
    """Total + per-modality breakdown for one cost period."""

    DEFAULT_CSS = """
    CostCard {
        layout: vertical;
        padding: 1 2;
        border: heavy $accent;
        height: auto;
        margin: 1 0;
    }
    CostCard #cost-total {
        text-style: bold;
        margin-bottom: 1;
    }
    CostCard .cost-modality-row {
        height: 1;
    }
    """

    def __init__(self, costs: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._costs: dict[str, Any] = costs or {}

    def compose(self) -> ComposeResult:
        yield Static(self._format_total(), id="cost-total")
        for modality in _MODALITIES:
            yield Static(
                self._format_modality(modality),
                id=f"cost-modality-{modality}",
                classes="cost-modality-row",
            )

    def update_costs(self, costs: dict[str, Any]) -> None:
        """Replace the underlying dict and re-render every line."""
        self._costs = costs or {}
        try:
            total = self.query_one("#cost-total", Static)
        except NoMatches:
            return
        total.update(self._format_total())
        for modality in _MODALITIES:
            self.query_one(f"#cost-modality-{modality}", Static).update(
                self._format_modality(modality)
            )

    # -- Formatting --------------------------------------------------

    def _format_total(self) -> str:
        period = self._costs.get("period") or "today"
        total = float(self._costs.get("total") or 0.0)
        return f"Total ({period}):  ${total:.4f}"

    def _format_modality(self, modality: str) -> str:
        by_modality = self._costs.get("by_modality") or {}
        info = by_modality.get(modality)
        if not isinstance(info, dict):
            return f"  {modality.upper()}:  --"
        cost = float(info.get("cost") or 0.0)
        count = info.get("request_count") or 0
        return f"  {modality.upper()}:  ${cost:.4f}  ({count} requests)"


__all__ = ["CostCard"]
