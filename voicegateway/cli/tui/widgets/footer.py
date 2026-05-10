"""``CounterFooter`` widget -- live counter row for the v0.1.1 TUI.

Mounted between the App's :class:`ContentSwitcher` and Textual's
built-in :class:`Footer`. Polls
:meth:`MetricsClient.list_costs(period='today')` on the active
client's ``poll_seconds`` cadence and renders today's total plus
the aggregate request count so the user always sees recent activity
without leaving whichever tab they're on.

The widget is its own small island: it does not depend on (or
duplicate) the Costs screen's :class:`CostCard`. The counter row is
a sentence, the card is a panel; they happen to read the same
endpoint but the layout costs of mounting a CostCard at the screen
foot are higher than mounting a single Static.

Phase 10 (Local-mode polish) extends this widget with the
``as of X minutes ago`` indicator computed from the SQLite file's
last-write timestamp; the polling timer registered here is the
substrate Phase 10 plugs into.
"""

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
        self.run_worker(self._refresh(), exclusive=True)
        app = cast("TUIApp", self.app)
        poll_seconds = float(getattr(app.client, "poll_seconds", 1.0))
        self.set_interval(poll_seconds, self._poll_tick)

    def _poll_tick(self) -> None:
        """Sync wrapper around the async refresh; matches LogsScreen."""
        self.run_worker(self._refresh(), exclusive=True)

    async def _refresh(self) -> None:
        """Fetch today's cost summary + render the counter line.

        Errors are swallowed for v0.1.1; the Phase-9 reconnection
        bullet adds the ``Reconnecting...`` indicator on top of the
        same poll loop. Until then a flaky daemon just leaves the
        last-known values on screen instead of pushing a stale
        zero.
        """
        app = cast("TUIApp", self.app)
        try:
            # ``include_pricing_source=True`` mirrors what CostsScreen
            # asks for so the daemon serves a single shape (any cache
            # warms once); CounterFooter does not read the
            # ``pricing_sources`` field but the extra payload is
            # negligible and the consistent contract is worth more.
            costs = await app.client.list_costs(
                period="today", include_pricing_source=True
            )
        except Exception:  # noqa: BLE001
            # The reconnection-indicator path: HttpClient marks
            # ``is_connected = False`` before re-raising; the next
            # tick re-renders with the "Reconnecting..." text so
            # the user sees the failure state without a stack trace.
            self._redraw()
            return
        self._costs = costs
        self._redraw()

    def _redraw(self) -> None:
        """Update the counter line, accounting for the connection state.

        Named ``_redraw`` rather than ``_render`` because Textual's
        Widget base class uses ``_render`` internally for the
        rendering pipeline; overriding it with a None-return method
        breaks the pipeline at runtime.

        In Local mode the row appends ``(as of X ago)`` so the user
        sees how stale the SQLite snapshot is; the suffix is
        omitted in Gateway mode where the daemon serves live data.
        """
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
    """Pure formatter so the counter line is unit-testable.

    Renders ``Today: $<total>   Requests: <N>``. ``N`` is the sum
    of per-modality request counts when the response carries
    ``by_modality`` (the daemon and LocalClient both populate it
    with ``per_modality=true``); falls back to ``request_count`` on
    a flat shape; falls back to ``--`` when the value is missing.

    Accepts ``None`` (pre-first-fetch state) and any non-dict input
    by returning a ``Today: ...`` placeholder so the row never
    renders ``Today: $None`` or raises.

    Local-mode suffix: when ``is_local=True`` and ``db_path`` resolves
    to an existing file, append ``(as of X ago)`` computed from the
    file's mtime. Helps the user see at a glance how stale the
    snapshot is when the daemon is not feeding fresh writes.
    """
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
    """Render ``"as of <Ns / Nmin / Nh / Nd> ago"`` from the file's
    mtime; returns ``None`` when the file does not exist (the
    Pilot smoke can pass a fake path -- no raise).

    Granularity steps are deliberately coarse: ``s`` for under a
    minute, ``min`` for under an hour, ``h`` for under a day,
    ``d`` after that. Coarser-than-precise -- the indicator answers
    "is this snapshot recent?" not "exactly how recent is this
    snapshot?", and the screen updates frequently enough that the
    user sees the last bucket flip.
    """
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
    """Sum per-modality request_count when present; flat fallback;
    ``--`` when neither is available. Returned as ``str`` so the
    formatter renders ``--`` directly without a ``int(--)`` cast.
    """
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
