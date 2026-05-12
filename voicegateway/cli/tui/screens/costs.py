"""Costs tab body (REQ-VG-TUI-003).

Renders today's total plus a per-modality breakdown (STT / LLM / TTS)
inside a :class:`CostCard`. Time-range cycles on the ``r`` key
through ``today`` -> ``this_week`` -> ``this_month`` -> ``today``;
the header line names the active range with bracket markers so the
indicator is unambiguous in plain ASCII (no Unicode arrows or emoji
per project rules).

Mounted inside :class:`TUIApp`'s :class:`ContentSwitcher` keyed
``costs``. Base class is :class:`Container` for the same reason as
:class:`SessionsScreen` -- the four tab panels mount peer-of-widgets
inside the App; ``Screen`` is reserved for modal navigation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Label

from voicegateway.cli.tui.widgets.cost_card import CostCard

if TYPE_CHECKING:  # pragma: no cover
    from voicegateway.cli.tui.app import TUIApp

#: Time-range cycle in display order. The daemon's
#: ``GET /v1/costs?period=...`` and storage's
#: ``get_cost_summary(period=...)`` both accept these strings; the
#: HTTP shape rewrites them server-side, the local shape passes
#: through. ``today`` is the v0.0.5 default that matches the web
#: dashboard's landing view.
_RANGES: tuple[str, ...] = ("today", "this_week", "this_month")


class CostsScreen(Container):
    """Total + per-modality breakdown with vim-style range cycle."""

    # ``can_focus = True`` so this screen is the focus target when
    # the tab switches: its BINDINGS only resolve when something
    # in the screen's DOM subtree (or the screen itself) holds
    # focus. Unlike SessionsScreen, which has a focusable
    # VerticalScroll child, CostsScreen has no focusable
    # descendants today (Label + Static modality rows are passive),
    # so the screen Container has to take focus directly.
    can_focus = True

    # Plain assignment (no ClassVar annotation) to match DOMNode's
    # wider BINDINGS union; same pattern as SessionsScreen.
    BINDINGS = [
        Binding("r", "cycle_range", "Range"),
    ]

    # Layout-only rules; cross-screen treatment for the header bar
    # lives in main.tcss as the ``.tab-header`` utility class.
    DEFAULT_CSS = """
    CostsScreen {
        layout: vertical;
        padding: 1;
    }
    """

    # Per-instance default; mutating ``self._range`` shadows this
    # class-level attribute. Same pattern as SessionsScreen._sort.
    _range: str = _RANGES[0]

    def compose(self) -> ComposeResult:
        yield Label(self._header_text(), id="costs-header", classes="tab-header")
        yield CostCard(id="cost-card")

    async def on_mount(self) -> None:
        await self.refresh_data()
        # Live refresh on the client's poll cadence so the totals
        # do not freeze after the first fetch (ContentSwitcher keeps
        # this screen mounted while other tabs are active; without
        # the interval the card would never re-poll for the rest of
        # the TUI's lifetime). Matches the SessionsScreen / LogsScreen
        # / CounterFooter pattern.
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
        """Fetch the active range + push the result into CostCard.

        ``include_pricing_source=True`` so the response carries the
        per-modality source stamps (``pricing_sources`` key) the
        :func:`stale_marker` helper reads to render the inline
        ``(as of X)`` indicator when a stamp is older than 24 h.
        """
        app = cast("TUIApp", self.app)
        try:
            costs = await app.client.list_costs(
                period=self._range, include_pricing_source=True
            )
        except Exception:  # noqa: BLE001
            # Daemon unreachable: keep the last-known card visible.
            # The CounterFooter reconnection indicator surfaces the
            # outage; clearing the card would discard the user's
            # last good view of cost state.
            return
        self.query_one("#cost-card", CostCard).update_costs(costs)

    # -- Actions -----------------------------------------------------

    def action_cycle_range(self) -> None:
        """``today`` -> ``this_week`` -> ``this_month`` -> ``today``."""
        idx = _RANGES.index(self._range)
        self._range = _RANGES[(idx + 1) % len(_RANGES)]
        header = self.query_one("#costs-header", Label)
        header.update(self._header_text())
        # exclusive=True so a fast double-press cancels the in-flight
        # worker rather than racing two refreshes against each other.
        self.run_worker(cast(Any, self.refresh_data), exclusive=True)

    # -- Helpers -----------------------------------------------------

    def _header_text(self) -> str:
        """Active-range indicator. Brackets mark the live mode so the
        plain-ASCII rendering reads unambiguously even on terminals
        that drop styling.
        """
        labels = [f"[{r}]" if r == self._range else r for r in _RANGES]
        return "Costs  |  Range:  " + "  ".join(labels) + "   (press `r` to cycle)"
