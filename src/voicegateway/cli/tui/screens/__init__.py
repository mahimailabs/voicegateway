"""Tab-panel screens for the TUI."""

from __future__ import annotations

from voicegateway.cli.tui.screens.costs_screen import CostsScreen
from voicegateway.cli.tui.screens.logs_screen import LogsScreen
from voicegateway.cli.tui.screens.providers_screen import ProvidersScreen
from voicegateway.cli.tui.screens.session_detail_screen import SessionDetailScreen
from voicegateway.cli.tui.screens.sessions_screen import SessionsScreen

__all__ = [
    "SessionsScreen",
    "SessionDetailScreen",
    "CostsScreen",
    "LogsScreen",
    "ProvidersScreen",
]
