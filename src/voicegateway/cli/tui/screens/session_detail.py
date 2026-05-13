"""Per-session drill-in modal (REQ-VG-TUI-002 detail view)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

if TYPE_CHECKING:  # pragma: no cover
    from voicegateway.cli.tui.app import TUIApp


class SessionDetailScreen(ModalScreen[None]):
    """Modal screen rendering the per-turn detail of one session."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
    ]

    DEFAULT_CSS = """
    SessionDetailScreen {
        align: center middle;
    }
    SessionDetailScreen > Container {
        width: 80;
        max-height: 30;
        padding: 1 2;
        background: $panel;
        border: thick $accent;
    }
    SessionDetailScreen #detail-title {
        text-style: bold;
        margin-bottom: 1;
    }
    """

    session_id: str

    def __init__(self, session_id: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.session_id = session_id

    def compose(self) -> ComposeResult:
        with Container():
            yield Static(f"Session: {self.session_id}", id="detail-title")
            yield VerticalScroll(id="detail-body")

    async def on_mount(self) -> None:
        await self._load_detail()

    async def _load_detail(self) -> None:
        app = cast("TUIApp", self.app)
        detail = await app.client.get_session_detail(self.session_id)
        body = self.query_one("#detail-body", VerticalScroll)
        await body.remove_children()
        if detail is None:
            await body.mount(Static("Session not found.", id="detail-not-found"))
            return
        await body.mount(Static(_format_detail(detail), id="detail-body-text"))


def _format_detail(detail: dict[str, Any]) -> str:
    """Pure formatter so it can be unit-tested without Textual."""
    project = detail.get("project") or "?"
    started = detail.get("started_at") or "?"
    ended = detail.get("ended_at") or "?"
    cost = float(detail.get("total_cost_usd") or 0.0)
    request_count = detail.get("request_count") or 0
    modalities = detail.get("modalities") or []
    providers = detail.get("providers") or []

    lines = [
        f"Project:        {project}",
        f"Started:        {started}",
        f"Ended:          {ended}",
        f"Total cost:     ${cost:.4f}",
        f"Request count:  {request_count}",
        f"Modalities:     {', '.join(str(m) for m in modalities)}",
        f"Providers:      {', '.join(str(p) for p in providers)}",
    ]

    by_modality = detail.get("by_modality") or {}
    if isinstance(by_modality, dict) and by_modality:
        lines.append("")
        lines.append("By modality:")
        for modality_name, info in by_modality.items():
            if isinstance(info, dict):
                m_cost = float(info.get("cost") or 0.0)
                m_count = info.get("request_count") or 0
                lines.append(
                    f"  {modality_name:>4}: ${m_cost:.4f}  ({m_count} requests)"
                )
    return "\n".join(lines)


__all__ = ["SessionDetailScreen", "_format_detail"]
