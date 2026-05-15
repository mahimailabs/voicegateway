"""Pilot tests for the Phase-7 help-overlay modal."""

from __future__ import annotations

from pathlib import Path

import pytest

from voicegateway.cli.tui import TUIApp
from voicegateway.cli.tui.data.local_client import LocalClient
from voicegateway.cli.tui.screens.help_screen import HelpOverlay
from voicegateway.cli.tui.widgets.session_row_widget import SessionRow  # noqa: F401
from voicegateway.services.storage_service import StorageService


@pytest.fixture
def help_app(tmp_path: Path) -> TUIApp:
    db = tmp_path / "voicegw.db"
    storage = StorageService(db)
    client = LocalClient(db_path=db, storage=storage)
    return TUIApp(client=client, is_local=True)


async def _settle(pilot, ticks: int = 8) -> None:
    for _ in range(ticks):
        await pilot.pause()


def _overlay_text(app: TUIApp) -> str:
    overlay = next(s for s in app.screen_stack if isinstance(s, HelpOverlay))
    return str(overlay.query_one("#help-body").renderable)


# ---------------------------------------------------------------------------
# ? open + dismiss
# ---------------------------------------------------------------------------


async def test_question_mark_opens_help_overlay(help_app: TUIApp) -> None:
    async with help_app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("question_mark")
        await _settle(pilot)
        assert any(isinstance(s, HelpOverlay) for s in help_app.screen_stack)


async def test_escape_dismisses_overlay(help_app: TUIApp) -> None:
    async with help_app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("question_mark")
        await _settle(pilot)
        await pilot.press("escape")
        await _settle(pilot)
        assert not any(isinstance(s, HelpOverlay) for s in help_app.screen_stack)


async def test_q_dismisses_overlay(help_app: TUIApp) -> None:
    """``q`` is the third dismissal key, matching SessionDetailScreen."""
    async with help_app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("question_mark")
        await _settle(pilot)
        await pilot.press("q")
        await _settle(pilot)
        assert not any(isinstance(s, HelpOverlay) for s in help_app.screen_stack)


# ---------------------------------------------------------------------------
# Cheatsheet content
# ---------------------------------------------------------------------------


async def test_overlay_lists_app_global_bindings(help_app: TUIApp) -> None:
    async with help_app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("question_mark")
        await _settle(pilot)
        text = _overlay_text(help_app)
        # All five App-level keys are documented.
        assert "Global" in text
        for key in ("q", "?", "1", "2", "3", "4", "tab", "shift+tab"):
            assert key in text, f"{key} missing from Global section"


@pytest.mark.parametrize(
    "tab_key,screen_name,expected_keys",
    [
        ("1", "SessionsScreen", ["s", "enter", "j", "k", "g", "G"]),
        ("2", "CostsScreen", ["r"]),
        ("3", "LogsScreen", ["/", "j", "k", "g", "G"]),
        ("4", "ProvidersScreen", ["t", "j", "k", "g", "G"]),
    ],
)
async def test_overlay_per_screen_section_matches_active_tab(
    help_app: TUIApp,
    tab_key: str,
    screen_name: str,
    expected_keys: list[str],
) -> None:
    """Switching tabs + opening the overlay renders the new tab's"""
    async with help_app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press(tab_key)
        await _settle(pilot)
        await pilot.press("question_mark")
        await _settle(pilot)
        text = _overlay_text(help_app)
        assert screen_name in text, f"{screen_name} section missing from overlay"
        for expected in expected_keys:
            assert expected in text, (
                f"{expected!r} key not listed in {screen_name} section"
            )


async def test_overlay_filters_show_false_aliases(help_app: TUIApp) -> None:
    """``h`` and ``l`` are bound on list screens as aliases of"""
    async with help_app.run_test() as pilot:
        await _settle(pilot)
        # Sessions tab is the default and carries h/l aliases.
        await pilot.press("question_mark")
        await _settle(pilot)
        text = _overlay_text(help_app)
        # Find the SessionsScreen section
        assert "SessionsScreen" in text
        sections = text.split("\n\n")
        sessions_section = next(s for s in sections if s.startswith("SessionsScreen"))
        # h/l should not appear as standalone keys in the cheatsheet
        # (they remain functional but hidden). The ``j`` and ``k``
        # entries do appear.
        lines = sessions_section.splitlines()
        keyed = [line.strip().split()[0] for line in lines if line.startswith("  ")]
        assert "h" not in keyed
        assert "l" not in keyed
        assert "j" in keyed
        assert "k" in keyed
