"""``CounterFooter`` widget -- live counter row for the TUI."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

if TYPE_CHECKING:  # pragma: no cover
    from voicegateway.cli.tui.app import TUIApp


class CounterFooter(Horizontal):
    """Single-line live-counter row above the Footer."""

    DEFAULT_CSS = """
    CounterFooter {
        height: 1;
        padding: 0 1;
        background: $boost;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Today: ...", id="counter-text")

    def on_mount(self) -> None:
        # Kick a refresh as a worker so on_mount stays sync; the
        # screen-level pattern matches LogsScreen's (iteration 26).
        self.run_worker(cast(Any, self._refresh), exclusive=True)
        app = cast("TUIApp", self.app)
        poll_seconds = float(getattr(app.client, "poll_seconds", 1.0))
        self.set_interval(poll_seconds, self._poll_tick)

    def _poll_tick(self) -> None:
        """Sync wrapper around the async refresh; matches LogsScreen."""
        self.run_worker(cast(Any, self._refresh), exclusive=True)

    async def _refresh(self) -> None:
        """Fetch today's cost summary + render the counter line."""
        app = cast("TUIApp", self.app)
        try:
            costs = await app.client.list_costs(
                period="today", include_pricing_source=True
            )
        except Exception:  # noqa: BLE001
            self._redraw()
            return
        self._costs = costs
        self._redraw()

    def _redraw(self) -> None:
        """Update the counter line, accounting for the connection state."""
        app = cast("TUIApp", self.app)
        is_connected = bool(getattr(app.client, "is_connected", True))
        text_widget = self.query_one("#counter-text", Static)
        if not is_connected:
            text_widget.update("Reconnecting to daemon...")
            return
        is_local = bool(getattr(app, "is_local", False))
        db_path: Path | None = None
        if is_local:
            raw = getattr(app.client, "_db_path", None)
            if raw is not None:
                db_path = Path(raw)
        text_widget.update(
            _format(
                getattr(self, "_costs", None),
                is_local=is_local,
                db_path=db_path,
            )
        )


def _format(
    costs: Any,
    *,
    is_local: bool = False,
    db_path: Path | None = None,
) -> str:
    """Pure formatter so the counter line is unit-testable."""
    if not isinstance(costs, dict):
        return "Today: ..."
    total = float(costs.get("total") or 0.0)
    requests = _aggregate_request_count(costs)
    base = f"Today: ${total:.4f}   Requests: {requests}"
    if is_local and db_path is not None:
        age = _format_age(db_path)
        if age:
            base = f"{base}   ({age})"
    return base


def _format_age(db_path: Path) -> str | None:
    """Render ``"as of <Ns / Nmin / Nh / Nd> ago"`` from the file's"""
    try:
        mtime = db_path.stat().st_mtime
    except OSError:
        return None
    seconds = max(0, int(time.time() - mtime))
    if seconds < 60:
        return f"as of {seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"as of {minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"as of {hours}h ago"
    days = hours // 24
    return f"as of {days}d ago"


def _aggregate_request_count(costs: dict[str, Any]) -> str:
    """Sum per-modality request_count when present; flat fallback;"""
    by_modality = costs.get("by_modality") or {}
    if isinstance(by_modality, dict) and by_modality:
        total = 0
        any_known = False
        for info in by_modality.values():
            if isinstance(info, dict) and "request_count" in info:
                try:
                    total += int(info.get("request_count") or 0)
                    any_known = True
                except (TypeError, ValueError):
                    continue
        if any_known:
            return str(total)
    flat = costs.get("request_count")
    if flat is not None:
        try:
            return str(int(flat))
        except (TypeError, ValueError):
            return "--"
    return "--"


__all__ = ["CounterFooter"]
