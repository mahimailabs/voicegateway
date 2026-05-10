"""Providers tab body (REQ-VG-TUI-005).

Lists configured providers consumed from
:meth:`MetricsClient.list_providers`. Each row renders the green /
red / gray indicator the Refinery names; the test shortcut + the
Local-mode "requires daemon" hint land in the next Phase-6 bullet
(``t`` keybinding) and Phase 10 (the [Local mode] header chip).

Mounted inside :class:`TUIApp`'s :class:`ContentSwitcher` keyed
``providers``. Base class is :class:`Container` for the same reason
as every other tab panel in v0.1.1 -- the four panels mount
peer-of-widgets simultaneously; ``Screen`` is reserved for modal
navigation (the Sessions detail drill-in is the canonical case).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import Label, Static

from voicegateway.cli.tui.widgets.provider_row import ProviderRow

if TYPE_CHECKING:  # pragma: no cover
    from voicegateway.cli.tui.app import TUIApp


class ProvidersScreen(Container):
    """List of configured providers + last-test indicator."""

    # ``can_focus = True`` so the screen-level BINDINGS the next
    # Phase-6 bullet adds (``t`` -> test_provider) resolve when this
    # tab is active. Same pattern as CostsScreen -- the screen
    # Container is the safe focus target before the ProviderRow list
    # has any rows to focus.
    can_focus = True

    DEFAULT_CSS = """
    ProvidersScreen {
        layout: vertical;
        padding: 1;
    }
    ProvidersScreen #providers-header {
        height: 1;
        padding: 0 1;
        background: $boost;
    }
    ProvidersScreen #providers-list {
        scrollbar-background: $surface;
    }
    ProvidersScreen .empty {
        height: 100%;
        content-align: center middle;
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("Providers", id="providers-header")
        yield VerticalScroll(id="providers-list")

    async def on_mount(self) -> None:
        await self.refresh_data()

    async def refresh_data(self) -> None:
        """Fetch the configured-provider list and re-render the rows.

        Mirrors :class:`SessionsScreen.refresh_data`'s pattern: drop
        every child + remount, since the list is small (typically
        single digits) and re-mounting keeps the code simple. The
        Phase-9 polling iteration will revisit if focus loss becomes
        a real complaint.
        """
        app = cast("TUIApp", self.app)
        providers = await app.client.list_providers()
        list_view = self.query_one("#providers-list", VerticalScroll)
        await list_view.remove_children()
        if not providers:
            await list_view.mount(Static("No providers configured.", classes="empty"))
            return
        for provider in providers:
            await list_view.mount(ProviderRow(provider))
