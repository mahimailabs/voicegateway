"""``SessionRow`` widget for the Sessions tab (REQ-VG-TUI-002).

One row per recent voice session. Renders four fields side-by-side
so the Sessions tab list reads like a terminal-native dashboard:

    HH:MM:SS   <duration>   $<cost>   <provider, ...>

The widget is a thin :class:`textual.widgets.Static` subclass that
formats the dict returned by :meth:`MetricsClient.list_sessions`
into a single fixed-width line. The Sessions screen mounts one row
per session inside a scrolling container; sort toggles re-render
the list rather than mutating individual rows.

The dict shape both clients return (see iteration 7's smoke test):

    {
        "id": "vg-...",
        "project": "default",
        "started_at": ISO 8601 str,
        "ended_at": ISO 8601 str,
        "modalities": ["llm", ...],
        "total_cost_usd": float,
        "request_count": int,
        "by_modality": {...},
        "providers": ["openai", ...],
    }
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from textual.widgets import Static

#: Formatted-row width budgets. Keep in sync with the Sessions
#: screen's column header (Phase 3 bullet 3) so the heading and the
#: rows align.
_TIME_WIDTH = 8  # HH:MM:SS
_DURATION_WIDTH = 8
_COST_WIDTH = 9  # "$0.00012" plus a couple of pad chars


class SessionRow(Static):
    """One row in the Sessions tab list."""

    DEFAULT_CSS = """
    SessionRow {
        height: 1;
    }
    SessionRow:focus {
        background: $accent 30%;
    }
    """

    can_focus = True

    def __init__(self, session: dict[str, Any], **kwargs: Any) -> None:
        self.session = session
        super().__init__(self._format(), **kwargs)

    def update_session(self, session: dict[str, Any]) -> None:
        """Replace the underlying dict and re-render the row."""
        self.session = session
        self.update(self._format())

    @property
    def session_id(self) -> str:
        """Storage-canonical id; the Sessions screen uses this to
        push the per-turn detail modal on Enter (Phase 3 bullet 4)."""
        return str(self.session.get("id", ""))

    # -- Formatting --------------------------------------------------

    def _format(self) -> str:
        started = format_time(self.session.get("started_at", ""))
        duration = format_duration(
            self.session.get("started_at", ""),
            self.session.get("ended_at", ""),
        )
        cost_value = self.session.get("total_cost_usd", 0.0) or 0.0
        cost = f"${float(cost_value):.4f}"
        providers_list = self.session.get("providers") or []
        providers = ", ".join(str(p) for p in providers_list)
        return (
            f"{started:<{_TIME_WIDTH}}  "
            f"{duration:<{_DURATION_WIDTH}}  "
            f"{cost:<{_COST_WIDTH}}  "
            f"{providers}"
        )


def format_time(iso: str) -> str:
    """Render ``HH:MM:SS`` from ISO 8601, ``--`` on parse failure."""
    if not iso:
        return "--"
    try:
        return datetime.fromisoformat(iso).strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return "--"


def format_duration(start: str, end: str) -> str:
    """Render duration as ``Ns`` (<60s) or ``MmNs`` (<1h) or
    ``HhMm`` (>=1h). Returns ``--`` when either bound is unparseable.
    """
    if not start or not end:
        return "--"
    try:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
    except (TypeError, ValueError):
        return "--"
    secs = max(0, int((end_dt - start_dt).total_seconds()))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m{secs % 60}s"
    return f"{secs // 3600}h{(secs % 3600) // 60}m"


__all__ = ["SessionRow", "format_time", "format_duration"]
