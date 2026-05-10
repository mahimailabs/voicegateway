"""Tab-panel screens for the v0.1.1 TUI.

Each tab in ``TUIApp``'s ``ContentSwitcher`` is mounted from one of
the four classes re-exported here. Phases 3-6 each replace one
placeholder body with the real implementation; the import path
(``voicegateway.cli.tui.screens.sessions:SessionsScreen`` etc.) and
the class name stay stable from this iteration onward so the
mounting code in ``app.py`` never changes.

Choice of base class. Each ``*Screen`` subclasses
:class:`textual.containers.Container`, NOT
:class:`textual.screen.Screen`. The four tab panels are mounted
simultaneously inside the App's ``ContentSwitcher`` (a peer-of-
widgets pattern), so :class:`Screen`'s screen-stack lifecycle hooks
(``on_screen_resume`` etc.) would not fire here. The ``Screen``
class is reserved for page-level navigation -- the session-detail
drill-in modal Phase 3 pushes via ``app.push_screen`` is the place
that contract applies. The ``Screen`` suffix in our class names
follows design.md's file-layout vocabulary (one "screen" per tab)
without imposing the Textual API class.
"""

from __future__ import annotations

from voicegateway.cli.tui.screens.costs import CostsScreen
from voicegateway.cli.tui.screens.logs import LogsScreen
from voicegateway.cli.tui.screens.providers import ProvidersScreen
from voicegateway.cli.tui.screens.session_detail import SessionDetailScreen
from voicegateway.cli.tui.screens.sessions import SessionsScreen

__all__ = [
    "SessionsScreen",
    "SessionDetailScreen",
    "CostsScreen",
    "LogsScreen",
    "ProvidersScreen",
]
