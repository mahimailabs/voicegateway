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
from textual.binding import Binding
from textual.widgets import ContentSwitcher, Footer, Header

from voicegateway.cli.tui.data import MetricsClient
from voicegateway.cli.tui.screens import (
    CostsScreen,
    LogsScreen,
    ProvidersScreen,
    SessionsScreen,
)

#: Tab ids in display order. ``ContentSwitcher`` mounts one
#: placeholder per id; ``initial=`` selects the first tab. The vim
#: ``1``-``4`` keys jump to ``_TAB_IDS[N-1]``; ``tab`` / ``shift-tab``
#: cycle forward / backward with wrap-around.
_TAB_IDS: tuple[str, ...] = ("sessions", "costs", "logs", "providers")


class TUIApp(App[None]):
    """Textual ``App`` subclass that owns the four-tab TUI.

    Constructor arguments are keyword-only so launch-site
    ``TUIApp(client=..., is_local=...).run()`` reads cleanly.

    Global vim keybindings (REQ-VG-TUI-006 partial; Phase 7 finishes
    the rest with the ``?`` overlay and the per-screen ``h/j/k/l``,
    ``gg/G``, ``/`` bindings):

    - ``q`` -- quit the app cleanly via Textual's built-in ``quit``.
    - ``?`` -- ``action_help``; rings the bell for now and gets
      replaced by a modal cheatsheet in Phase 7.
    - ``1`` / ``2`` / ``3`` / ``4`` -- jump to Sessions / Costs /
      Logs / Providers via :meth:`action_switch_tab`.
    - ``tab`` / ``shift-tab`` -- cycle forward / backward through
      ``_TAB_IDS`` via :meth:`action_cycle_tab`. Both are
      ``priority=True`` so the App-level binding wins over Textual's
      default focus traversal (vim users expect tab-switching here).

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

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("question_mark", "help", "Help"),
        Binding("1", "switch_tab('sessions')", "Sessions"),
        Binding("2", "switch_tab('costs')", "Costs"),
        Binding("3", "switch_tab('logs')", "Logs"),
        Binding("4", "switch_tab('providers')", "Providers"),
        # priority=True so tab/shift-tab cycle tabs instead of moving
        # widget focus -- vim users expect tab-switching at this level.
        # Per-screen focus traversal stays on h/j/k/l (Phase 7).
        Binding("tab", "cycle_tab(1)", "Next tab", priority=True),
        Binding("shift+tab", "cycle_tab(-1)", "Prev tab", priority=True),
    ]

    def __init__(self, *, client: MetricsClient, is_local: bool) -> None:
        super().__init__()
        self.client = client
        self.is_local = is_local

    def compose(self) -> ComposeResult:
        """Mount the header / four-tab switcher / footer spine.

        Each ``*Screen`` body lives in its own module under
        ``voicegateway.cli.tui.screens``; Phases 3-6 each replace one
        placeholder body without renaming the class or changing the
        import path, so the mounting code below never has to move.
        The id-strings stay stable from iteration 10 onward; the vim
        ``1``-``4`` shortcuts and the tab-cycle action target them
        directly.
        """
        yield Header()
        with ContentSwitcher(initial=_TAB_IDS[0], id="content"):
            yield SessionsScreen(id=_TAB_IDS[0])
            yield CostsScreen(id=_TAB_IDS[1])
            yield LogsScreen(id=_TAB_IDS[2])
            yield ProvidersScreen(id=_TAB_IDS[3])
        yield Footer()

    # -- Actions -----------------------------------------------------
    #
    # Textual resolves a binding's ``action`` string to ``action_<name>``
    # on the App. Each method below maps to one binding above.

    def action_switch_tab(self, tab_id: str) -> None:
        """Jump the ``ContentSwitcher`` to ``tab_id``.

        Bound by the ``1`` / ``2`` / ``3`` / ``4`` keys via
        ``switch_tab('<id>')`` in BINDINGS so the four jumps share
        one parameterised action.
        """
        switcher = self.query_one("#content", ContentSwitcher)
        switcher.current = tab_id

    def action_cycle_tab(self, delta: int) -> None:
        """Cycle through ``_TAB_IDS`` by ``delta`` (wrap-around).

        ``delta=1`` is ``tab``; ``delta=-1`` is ``shift-tab``. The
        modulo wrap means hitting ``tab`` from the last tab lands
        back on Sessions, matching the vim-style expectation users
        carry over from buffer/window cycling.
        """
        switcher = self.query_one("#content", ContentSwitcher)
        current = switcher.current or _TAB_IDS[0]
        # ``current`` is set by ``initial=`` and by switch_tab; it is
        # always a known tab id, but fall back defensively in case a
        # future iteration introduces an id outside the tuple.
        try:
            idx = _TAB_IDS.index(current)
        except ValueError:
            idx = 0
        new_idx = (idx + delta) % len(_TAB_IDS)
        switcher.current = _TAB_IDS[new_idx]

    def action_help(self) -> None:
        """Help-overlay placeholder.

        Phase 7 (vim keybinding pass) replaces this with a modal
        cheatsheet that reads each screen's ``KEYBINDINGS`` class
        attribute. Until then we ring the bell so a manual press of
        ``?`` produces a visible signal that the binding wired
        through correctly.
        """
        self.bell()


__all__ = ["TUIApp"]
