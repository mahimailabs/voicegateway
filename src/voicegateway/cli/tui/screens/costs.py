"""Costs tab body"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Label

from voicegateway.cli.tui.widgets.cost_card import CostCard

if TYPE_CHECKING:  # pragma: no cover
    from voicegateway.cli.tui.app import TUIApp

_RANGES: tuple[str, ...] = ("today", "this_week", "this_month")


class CostsScreen(Container):
    """Total + per-modality breakdown with vim-style range cycle."""

    can_focus = True

    BINDINGS = [
        Binding("r", "cycle_range", "Range"),
    ]

    DEFAULT_CSS = """
    CostsScreen {
        layout: vertical;
        padding: 1;
    }
    """

    _range: str = _RANGES[0]

    def compose(self) -> ComposeResult:
        yield Label(self._header_text(), id="costs-header", classes="tab-header")
        yield CostCard(id="cost-card")

    async def on_mount(self) -> None:
        await self.refresh_data()

        app = cast("TUIApp", self.app)
        poll_seconds = float(getattr(app.client, "poll_seconds", 1.0))
        self.set_interval(poll_seconds, self._poll_tick)

    def _poll_tick(self) -> None:
        """Sync wrapper around the async refresh; ``set_interval``
        accepts only sync callbacks. ``exclusive=True`` so a poll
        tick that lands while ``action_cycle_range`` is mid-fetch
        cancels the older worker rather than racing two refreshes.
        """
        self.run_worker(cast(Any, self.refresh_data), exclusive=True)

    async def refresh_data(self) -> None:
        """Fetch the active range + push the result into CostCard."""
        app = cast("TUIApp", self.app)
        try:
            costs = await app.client.list_costs(
                period=self._range, include_pricing_source=True
            )
        except Exception:  # noqa: BLE001
            return
        self.query_one("#cost-card", CostCard).update_costs(costs)

    # -- Actions -----------------------------------------------------

    def action_cycle_range(self) -> None:
        """``today`` -> ``this_week`` -> ``this_month`` -> ``today``."""
        idx = _RANGES.index(self._range)
        self._range = _RANGES[(idx + 1) % len(_RANGES)]
        header = self.query_one("#costs-header", Label)
        header.update(self._header_text())

        self.run_worker(cast(Any, self.refresh_data), exclusive=True)

    # -- Helpers -----------------------------------------------------

    def _header_text(self) -> str:
        """Active-range indicator. Brackets mark the live mode so the
        plain-ASCII rendering reads unambiguously even on terminals
        that drop styling.
        """
        labels = [f"[{r}]" if r == self._range else r for r in _RANGES]
        return "Costs  |  Range:  " + "  ".join(labels) + "   (press `r` to cycle)"
