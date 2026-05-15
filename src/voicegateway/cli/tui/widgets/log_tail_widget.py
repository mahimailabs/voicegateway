"""``LogTail`` widget for the Logs tab."""

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

        self._seen_ids: set[str] = set()

        self._all_entries: list[dict[str, Any]] = []

        self._filter: str | None = None

    def append_entries(self, entries: list[dict[str, Any]]) -> None:
        """Append every entry whose id has not been seen yet."""
        for entry in entries:
            entry_id = str(entry.get("id", ""))
            if entry_id:
                if entry_id in self._seen_ids:
                    continue
                self._seen_ids.add(entry_id)
            self._all_entries.append(entry)
            if self._matches_filter(entry):
                self.write(format_entry(entry))

    def set_filter(self, substring: str | None) -> None:
        """Apply (or clear) a case-insensitive substring filter."""
        self._filter = substring or None
        self.clear()
        for entry in self._all_entries:
            if self._matches_filter(entry):
                self.write(format_entry(entry))

    def reset(self) -> None:
        """Clear the visible buffer + the de-dup set + filter state."""
        self.clear()
        self._seen_ids.clear()
        self._all_entries.clear()
        self._filter = None

    def _matches_filter(self, entry: dict[str, Any]) -> bool:
        if self._filter is None:
            return True
        return self._filter.lower() in format_entry(entry).lower()


def format_entry(entry: dict[str, Any]) -> str:
    """Render one request row as a single fixed-width line."""
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
    """Render epoch (float / int) or ISO 8601 (str) as ``HH:MM:SS``."""
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
