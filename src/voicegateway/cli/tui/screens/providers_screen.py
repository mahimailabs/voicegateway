"""Providers tab body"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.widgets import Label, Static

from voicegateway.cli.tui.data.exceptions import LocalModeUnsupportedError
from voicegateway.cli.tui.screens.focus_helpers import FocusRowsMixin
from voicegateway.cli.tui.widgets.provider_row import ProviderRow

if TYPE_CHECKING:  # pragma: no cover
    from voicegateway.cli.tui.app import TUIApp


class ProvidersScreen(FocusRowsMixin, Container):
    """List of configured providers + last-test indicator + ``t`` shortcut."""

    can_focus = True

    BINDINGS = [
        Binding("t", "test_provider", "Test"),
        Binding("j", "focus_next_row", "Down"),
        Binding("k", "focus_prev_row", "Up"),
        Binding("g", "focus_first_row", "First"),
        Binding("G", "focus_last_row", "Last"),
        Binding("h", "focus_prev_row", "Up", show=False),
        Binding("l", "focus_next_row", "Down", show=False),
    ]

    def _focusable_rows(self) -> list[ProviderRow]:
        return list(
            self.query_one("#providers-list", VerticalScroll).query(ProviderRow)
        )

    DEFAULT_CSS = """
    ProvidersScreen {
        layout: vertical;
        padding: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("Providers", id="providers-header", classes="tab-header")
        yield VerticalScroll(id="providers-list", classes="tui-list")

    async def on_mount(self) -> None:
        await self.refresh_data()

    # -- Actions -----------------------------------------------------

    def action_test_provider(self) -> None:
        """Test the focused provider's key against the upstream API."""
        focused = self.app.focused
        if not isinstance(focused, ProviderRow):
            return
        self.run_worker(self._run_test(focused), exclusive=True)

    async def _run_test(self, row: ProviderRow) -> None:
        """Drive ``client.test_provider`` against ``row``."""
        app = cast("TUIApp", self.app)
        try:
            result = await app.client.test_provider(row.provider_id)
        except LocalModeUnsupportedError as exc:
            app.notify(
                str(exc),
                severity="warning",
                title="Action requires the daemon",
                timeout=4.0,
            )
            return
        except Exception as exc:  # noqa: BLE001
            app.notify(
                f"{row.provider_id}: {exc}",
                severity="error",
                title="Test failed",
                timeout=4.0,
            )
            return

        new_provider = dict(row.provider)
        new_status = str(result.get("status")) if isinstance(result, dict) else "ok"
        new_provider["status"] = new_status
        row.update_provider(new_provider)
        app.notify(
            f"{row.provider_id}: {new_status}",
            severity="information",
            timeout=2.0,
        )

    async def refresh_data(self) -> None:
        """Fetch the configured-provider list and re-render the rows."""
        app = cast("TUIApp", self.app)
        try:
            providers = await app.client.list_providers()
        except Exception:  # noqa: BLE001
            return
        list_view = self.query_one("#providers-list", VerticalScroll)
        await list_view.remove_children()
        if not providers:
            await list_view.mount(
                Static("No providers configured.", classes="empty-state")
            )
            return
        for provider in providers:
            await list_view.mount(ProviderRow(provider))
