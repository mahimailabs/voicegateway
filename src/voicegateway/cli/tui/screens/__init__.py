"""Tab-panel screens for the TUI."""

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
