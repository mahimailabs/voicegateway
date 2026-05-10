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
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Footer

from voicegateway.cli.tui.data import MetricsClient
from voicegateway.cli.tui.screens import (
    CostsScreen,
    LogsScreen,
    ProvidersScreen,
    SessionsScreen,
)
from voicegateway.cli.tui.widgets.footer import CounterFooter
from voicegateway.cli.tui.widgets.header import HeaderBar

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

    # CSS_PATH points at the TCSS brand foundation; Textual resolves
    # the path relative to this module file. Per-widget DEFAULT_CSS
    # owns component layout; main.tcss owns the cross-screen brand
    # chrome (focus rings, accent colour, active-tab indicator).
    CSS_PATH = "styles/main.tcss"

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
        yield HeaderBar(is_local=self.is_local, id="header-bar")
        with ContentSwitcher(initial=_TAB_IDS[0], id="content"):
            yield SessionsScreen(id=_TAB_IDS[0])
            yield CostsScreen(id=_TAB_IDS[1])
            yield LogsScreen(id=_TAB_IDS[2])
            yield ProvidersScreen(id=_TAB_IDS[3])
        # Phase-9 live counter row -- polls list_costs on the
        # client's poll_seconds cadence and updates in place.
        yield CounterFooter(id="counter-footer")
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
        self._focus_active_tab()

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
        self._focus_active_tab()

    def _focus_active_tab(self) -> None:
        """Move keyboard focus into the active tab.

        Each tab declares its own per-tab vim BINDINGS (``s`` on
        SessionsScreen, ``r`` on CostsScreen, ``/`` on LogsScreen,
        ``t`` on ProvidersScreen). Textual's binding resolution
        starts from the focused widget and walks up the DOM tree,
        so a screen's bindings only fire when something in its
        subtree holds focus. ContentSwitcher does not transfer
        focus on ``current=`` change, so this method does it
        explicitly: prefer the new tab's first focusable
        descendant, fall back to the screen Container itself when
        none of its children are focusable (e.g. CostsScreen,
        whose Label + Static rows are passive).
        """
        switcher = self.query_one("#content", ContentSwitcher)
        if switcher.current is None:
            return
        new_active = switcher.query_one(f"#{switcher.current}")
        # walk_children's annotation is DOMNode; the runtime values
        # are Widget instances so the isinstance guard satisfies mypy
        # without a runtime cost. ``descendant.display`` filters
        # out widgets the screen has hidden (e.g. LogsScreen's
        # filter Input is mounted but display=False until ``/``
        # reveals it); without this guard the hidden Input gets
        # focused and number keys land in its buffer instead of
        # triggering the App's tab-switch action.
        for descendant in new_active.walk_children():
            if (
                isinstance(descendant, Widget)
                and descendant.can_focus
                and descendant.display
            ):
                descendant.focus()
                return
        if new_active.can_focus:
            new_active.focus()

    def action_help(self) -> None:
        """Push the keybinding cheatsheet modal.

        Reads the active tab's BINDINGS plus the App's own to build
        a two-section cheatsheet at modal-mount time, so adding a
        new binding to any screen surfaces in the overlay
        automatically without a parallel docs list to maintain.
        """
        # Lazy import: the help screen lives under
        # voicegateway.cli.tui.screens, which the package's
        # __init__.py also re-exports SessionsScreen / CostsScreen
        # / etc. from. Importing at module scope here would create
        # a circular load between cli.tui.app and the screens
        # package.
        from voicegateway.cli.tui.screens.help import HelpOverlay

        self.push_screen(HelpOverlay())


__all__ = ["TUIApp"]
