"""Widget package for the v0.1.1 TUI.

Each widget is one row / card / shape that a screen composes. Phase 3
lands :class:`SessionRow`, Phase 4 :class:`CostCard`, Phase 5
:class:`LogTail`, and Phase 6 :class:`ProviderRow`. Phase 10's local-
mode polish lands the project-specific Header / Footer widgets that
swap the Textual built-ins.
"""

from __future__ import annotations

from voicegateway.cli.tui.widgets.cost_card import CostCard
from voicegateway.cli.tui.widgets.log_tail import LogTail
from voicegateway.cli.tui.widgets.provider_row import ProviderRow
from voicegateway.cli.tui.widgets.session_row import SessionRow

__all__ = ["CostCard", "LogTail", "ProviderRow", "SessionRow"]
