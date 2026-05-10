"""``CostCard`` widget for the Costs tab (REQ-VG-TUI-003).

Renders the total cost across the active period plus a per-modality
breakdown (STT / LLM / TTS) consumed from
:meth:`MetricsClient.list_costs`. The Costs screen mounts one card
per period; the time-range selector swaps the underlying dict via
:meth:`CostCard.update_costs` rather than re-mounting so the card's
focus / scroll state survives.

Expected dict shape (matches the daemon's ``/v1/costs?per_modality=
true`` response and LocalClient's normalised local-mode shape):

    {
        "period": "today",
        "total": float,
        "by_modality": {
            "stt": {"cost": float, "request_count": int},
            "llm": {"cost": float, "request_count": int},
            "tts": {"cost": float, "request_count": int},
        },
        # Phase-4 freshness bullet adds:
        # "pricing_sources": {"stt": {...}, "llm": {...}, "tts": {...}}
    }
"""

from __future__ import annotations

from datetime import date
from typing import Any

from textual.app import ComposeResult
from textual.containers import Container
from textual.css.query import NoMatches
from textual.widgets import Static

#: Modalities rendered in display order. STT first because the
#: Refinery names it as the conversation entry-point cost (mic ->
#: transcript) -- the same order the web dashboard uses.
_MODALITIES: tuple[str, ...] = ("stt", "llm", "tts")

#: Threshold past which we consider a pricing-source stamp stale
#: enough to surface inline. Matches the web dashboard's 24-hour
#: window so the two surfaces stay consistent.
_STALENESS_HOURS = 24.0


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
        """Replace the underlying dict and re-render every line.

        The Costs screen calls this on time-range change and on the
        polling refresh in Gateway mode (Phase 9). Avoids
        re-mounting the card (which would clobber focus + scroll
        state) by mutating the four Statics in place.

        Safe to call before mount: the dict update always succeeds,
        and ``compose()`` reads the fresh values when it eventually
        runs. Only the in-place re-render is gated on the children
        existing.
        """
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
        base = f"  {modality.upper()}:  ${cost:.4f}  ({count} requests)"
        sources = self._costs.get("pricing_sources") or {}
        marker = stale_marker(
            sources.get(modality) if isinstance(sources, dict) else None
        )
        return base + marker


def stale_marker(source: Any, threshold_hours: float = _STALENESS_HOURS) -> str:
    """Return ``  (as of YYYY-MM-DD)`` when ``source`` carries a date
    older than ``threshold_hours``; empty string otherwise.

    The pricing catalog stamps source strings as ``"<source>@<token>"``
    (verified at runtime against
    :func:`voicegateway.pricing.catalog.pricing_source`):

    - STT / TTS use the local catalog, e.g. ``"voicegateway-catalog@2026-05-04"``
      where the token is an ISO date.
    - LLM uses ``genai-prices``, e.g. ``"genai-prices@0.0.57"`` where the
      token is a SemVer version. We only flag staleness for tokens
      that parse as ISO dates; version strings stay un-marked because
      we do not infer their age from the version number.

    The double-space prefix lines up with the modality row's column
    spacing so the marker reads as a trailing annotation, not a new
    column. Matches the web dashboard's "as of X" treatment.
    """
    if not source or not isinstance(source, str) or "@" not in source:
        return ""
    _, token = source.rsplit("@", 1)
    try:
        stamp = date.fromisoformat(token)
    except (TypeError, ValueError):
        return ""
    age_hours = (date.today() - stamp).days * 24
    if age_hours > threshold_hours:
        return f"  (as of {token})"
    return ""


__all__ = ["CostCard", "stale_marker"]
