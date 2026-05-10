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
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.widgets import Label, Static

from voicegateway.cli.tui.data.exceptions import LocalModeUnsupportedError
from voicegateway.cli.tui.screens._focus import FocusRowsMixin
from voicegateway.cli.tui.widgets.provider_row import ProviderRow

if TYPE_CHECKING:  # pragma: no cover
    from voicegateway.cli.tui.app import TUIApp


class ProvidersScreen(FocusRowsMixin, Container):
    """List of configured providers + last-test indicator + ``t`` shortcut."""

    # ``can_focus = True`` so the screen-level BINDINGS resolve when
    # this tab is active. Same pattern as CostsScreen -- the screen
    # Container is the safe focus target before any ProviderRow takes
    # focus.
    can_focus = True

    BINDINGS = [
        Binding("t", "test_provider", "Test"),
        Binding("j", "focus_next_row", "Down"),
        Binding("k", "focus_prev_row", "Up"),
        Binding("g", "focus_first_row", "First"),
        Binding("G", "focus_last_row", "Last"),
        # h/l alias k/j for vim muscle memory; hidden from the
        # footer hint so the documented contract stays j/k/g/G.
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

    # -- Actions -----------------------------------------------------

    def action_test_provider(self) -> None:
        """Test the focused provider's key against the upstream API.

        Reads ``self.app.focused`` rather than walking the list
        because ``t`` only makes sense on a focused row -- exactly
        the SessionsScreen Enter-to-detail contract. Falls through
        silently when focus is on the screen Container itself or any
        non-row widget.

        Test work runs in a worker so the UI stays responsive while
        the upstream HTTPS round-trip completes; ``exclusive=True``
        means a fast double-press cancels the in-flight worker
        rather than racing two parallel test calls.
        """
        focused = self.app.focused
        if not isinstance(focused, ProviderRow):
            return
        self.run_worker(self._run_test(focused), exclusive=True)

    async def _run_test(self, row: ProviderRow) -> None:
        """Drive ``client.test_provider`` against ``row``.

        On success: pull the new ``status`` from the response and
        push it through :meth:`ProviderRow.update_provider` so the
        indicator + colour flip in place (focus + scroll state
        survive). On :class:`LocalModeUnsupportedError`: surface a
        notification naming the daemon-required action. Any other
        exception surfaces as a generic error notification with the
        upstream message so the user sees what went wrong.

        Locked decision 4 + 5 (see design.md section 4): the Local
        mode write-path raises with a machine-readable ``feature``
        attribute; we render that verbatim through the notification
        so the user sees ``test_provider`` as the unsupported
        feature name. Keeps the error surface honest without
        rewriting it through a translation layer.
        """
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

        # Mutate a copy so the original dict the row holds stays
        # source-of-truth-after-update; ``update_provider`` swaps
        # the indicator + CSS class in place.
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
