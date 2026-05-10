"""TUIApp shell for ``voicegw tui`` (v0.1.1).

This iteration scaffolds the spine the next four phases plug into:

- Phase 2 next bullets land global vim ``BINDINGS`` (q, ?, 1-4,
  tab/shift-tab) and the Typer ``tui`` command that calls
  :func:`run`.
- Phases 3-6 swap each ``Static`` placeholder in the
  ``ContentSwitcher`` for the real ``Sessions`` / ``Costs`` /
  ``Logs`` / ``Providers`` :class:`textual.screen.Screen` subclass.
- Phases 9-10 replace the Textual built-in :class:`Header` /
  :class:`Footer` with the project-specific widgets that carry the
  daemon-status chip, the persistent ``[Local mode]`` chip (locked
  decision 5), and the live counters.

The constructor takes the resolved :class:`MetricsClient` and the
``is_local`` flag at launch time so screens consume them via
``self.app.client`` / ``self.app.is_local`` without redoing mode
selection at runtime.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import ContentSwitcher, Footer, Header, Static

from voicegateway.cli.tui.data import MetricsClient

#: Tab ids in display order. ``ContentSwitcher`` mounts one
#: placeholder per id; ``initial=`` selects the first tab. Phase 2's
#: vim BINDINGS task wires ``1-4`` to switch among these four ids.
_TAB_IDS: tuple[str, ...] = ("sessions", "costs", "logs", "providers")


class TUIApp(App[None]):
    """Textual ``App`` subclass that owns the four-tab TUI.

    Constructor arguments are keyword-only so launch-site
    ``TUIApp(client=..., is_local=...).run()`` reads cleanly.

    Attributes:
        client: the :class:`MetricsClient` resolved by
            :func:`voicegateway.cli.tui.data.factory.make_client` at
            launch time. Screens read this via ``self.app.client``
            so they never branch on which backend is live.
        is_local: ``True`` when ``--local`` was passed (locked
            decision 5). Phase 10's header widget reads this to
            render the persistent ``[Local mode]`` chip; the screens
            read it to suppress write-path actions cleanly.
    """

    CSS = ""  # populated in Phase 8 (TCSS styling pass)

    def __init__(self, *, client: MetricsClient, is_local: bool) -> None:
        super().__init__()
        self.client = client
        self.is_local = is_local

    def compose(self) -> ComposeResult:
        """Mount the header / four-tab switcher / footer spine.

        Each :class:`Static` is a placeholder that Phases 3-6
        replace with the real Screen subclass. The id-strings are
        the binding targets for the upcoming ``1-4`` vim shortcuts,
        so they are stable from this iteration onward.
        """
        yield Header()
        with ContentSwitcher(initial=_TAB_IDS[0], id="content"):
            yield Static("Sessions placeholder", id=_TAB_IDS[0])
            yield Static("Costs placeholder", id=_TAB_IDS[1])
            yield Static("Logs placeholder", id=_TAB_IDS[2])
            yield Static("Providers placeholder", id=_TAB_IDS[3])
        yield Footer()


__all__ = ["TUIApp"]
