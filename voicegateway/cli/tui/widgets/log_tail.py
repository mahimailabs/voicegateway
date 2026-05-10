"""``LogTail`` widget for the Logs tab (REQ-VG-TUI-004).

Wraps Textual's :class:`textual.widgets.RichLog` with two affordances
the Logs screen needs:

- :meth:`LogTail.append_entries` takes a list of request-row dicts
  (from :meth:`MetricsClient.list_logs`) and writes one line per
  entry, de-duped on the row's ``id`` field so a polling loop that
  re-fetches overlapping windows does not re-append rows.
- :meth:`LogTail.reset` clears the buffer and the de-dup set
  together so the screen can switch projects / modalities cleanly.

The widget itself is passive: it does NOT poll. The Logs screen
owns the polling loop and calls ``append_entries`` with the new
batch each tick. ``RichLog``'s built-in ``auto_scroll`` keeps the
bottom in view unless the user scrolls up, which is the Refinery's
"now tailing" / "manual scrolled up" contract for free.

Expected entry shape (matches storage.get_recent_requests + the
daemon's /v1/logs response):

    {
        "id": str,
        "timestamp": float,            # epoch seconds
        "modality": "stt" | "llm" | "tts",
        "provider": str,
        "model_id": str,
        "project": str,
        "cost_usd": float,
        "status": "success" | ...,
        "total_latency_ms": float | None,
        "ttfb_ms": float | None,
    }
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from textual.widgets import RichLog


class LogTail(RichLog):
    """Append-only request-log viewer with de-dup."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("auto_scroll", True)
        kwargs.setdefault("highlight", False)
        kwargs.setdefault("markup", False)
        kwargs.setdefault("wrap", False)
        super().__init__(**kwargs)
        # Bounded growth is a Phase-9 polish concern; v0.1.1 lets the
        # set grow with the buffer because Textual already caps the
        # rendered line count via ``max_lines`` on RichLog.
        self._seen_ids: set[str] = set()

    def append_entries(self, entries: list[dict[str, Any]]) -> None:
        """Append every entry whose id has not been seen yet.

        The Logs screen polls :meth:`MetricsClient.list_logs` on a
        cadence; the windowed result overlaps the previous fetch by
        design (so a slow request that finishes after the cutoff
        still surfaces). De-dup on ``entry["id"]`` drops the
        already-rendered rows so the visible tail reads forward-only.

        Entries without an ``id`` (e.g. a synthetic test fixture
        that omits the field) are appended unconditionally; the
        de-dup set never grows for them, which mirrors the
        screen's "stream every line we see" expectation when the
        upstream cannot identify rows.
        """
        for entry in entries:
            entry_id = str(entry.get("id", ""))
            if entry_id:
                if entry_id in self._seen_ids:
                    continue
                self._seen_ids.add(entry_id)
            self.write(format_entry(entry))

    def reset(self) -> None:
        """Clear the visible buffer + the de-dup set."""
        self.clear()
        self._seen_ids.clear()


def format_entry(entry: dict[str, Any]) -> str:
    """Render one request row as a single fixed-width line.

    Layout: ``HH:MM:SS  STT  provider     model_id              $cost   Nms  status``

    The widths are tuned to fit on an 80-column terminal without
    truncation for typical providers + model ids; longer model
    strings overflow into the next column rather than truncate
    (Phase 8's TCSS pass + the Phase-9 narrow-width handling will
    revisit if a real user complains).
    """
    ts = format_timestamp(entry.get("timestamp"))
    modality = str(entry.get("modality") or "").upper()
    provider = str(entry.get("provider") or "?")
    model = str(entry.get("model_id") or "?")
    cost = float(entry.get("cost_usd") or 0.0)
    latency = entry.get("total_latency_ms") or entry.get("ttfb_ms") or 0
    try:
        latency_int = int(float(latency))
    except (TypeError, ValueError):
        latency_int = 0
    status = str(entry.get("status") or "?")
    return (
        f"{ts}  {modality:<3}  {provider:<10}  "
        f"{model:<20}  ${cost:.4f}  {latency_int:>5}ms  {status}"
    )


def format_timestamp(ts: Any) -> str:
    """Render epoch (float / int) or ISO 8601 (str) as ``HH:MM:SS``.

    Returns ``--:--:--`` on parse failure or ``None`` so the row
    column alignment never breaks; the Logs screen's empty-state
    message handles the no-data case at a higher level.
    """
    if ts is None:
        return "--:--:--"
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S")
        except (OSError, OverflowError, ValueError):
            return "--:--:--"
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts).strftime("%H:%M:%S")
        except (TypeError, ValueError):
            return "--:--:--"
    return "--:--:--"


__all__ = ["LogTail", "format_entry", "format_timestamp"]
