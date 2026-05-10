"""Tests for TUIApp construction + the Phase-2 keybinding wiring.

Covers:

- :class:`TUIApp` construction with both real client implementations
  (HttpClient via ``httpx.MockTransport``, LocalClient against a
  tempdir SQLite) and the constructor's invariant that ``client``
  and ``is_local`` survive untouched onto instance attributes.
- The four placeholder Container screens mount under their
  documented ids.
- The Phase-2 vim ``BINDINGS`` fire end-to-end through
  ``app.run_test()``: ``q`` exits cleanly, ``?`` calls
  :meth:`TUIApp.action_help` (the bell stub Phase 7 will swap for
  the modal cheatsheet), ``1`` / ``4`` jumps and ``tab`` /
  ``shift-tab`` cycles route through the
  :class:`textual.widgets.ContentSwitcher`.
- ``voicegw tui`` (no ``--local``) prints the daemon-down message
  and exits 1 when the preflight ``/health`` probe fails.
- ``voicegw tui --local`` skips the preflight regardless (locked
  decision 5 covers the case where the daemon happens to be down
  and the user explicitly asked for Local mode).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import click
import httpx
import pytest
from typer.testing import CliRunner

from voicegateway.cli import app as cli_app
from voicegateway.cli.tui import TUIApp
from voicegateway.cli.tui.app import _TAB_IDS
from voicegateway.cli.tui.data.http import HttpClient
from voicegateway.cli.tui.data.local import LocalClient
from voicegateway.cli.tui.screens import (
    CostsScreen,
    LogsScreen,
    ProvidersScreen,
    SessionsScreen,
)

runner = CliRunner()


def _plain(output: str) -> str:
    """Strip ANSI so substring asserts survive ``FORCE_COLOR=1`` CI."""
    return click.unstyle(output)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def http_client() -> HttpClient:
    """HttpClient backed by an empty MockTransport."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    inner = httpx.AsyncClient(base_url="http://daemon.test", transport=transport)
    return HttpClient(url="http://daemon.test", http_client=inner)


@pytest.fixture
def local_client(tmp_path: Path) -> LocalClient:
    return LocalClient(db_path=tmp_path / "voicegw.db")


# ---------------------------------------------------------------------------
# TUIApp construction
# ---------------------------------------------------------------------------


def test_tuiapp_constructs_with_http_client(http_client: HttpClient) -> None:
    app = TUIApp(client=http_client, is_local=False)
    assert app.client is http_client
    assert app.is_local is False


def test_tuiapp_constructs_with_local_client(
    local_client: LocalClient,
) -> None:
    app = TUIApp(client=local_client, is_local=True)
    assert app.client is local_client
    assert app.is_local is True


# ---------------------------------------------------------------------------
# Mount + four placeholder screens render under matching ids
# ---------------------------------------------------------------------------


async def test_tuiapp_mounts_four_screens(local_client: LocalClient) -> None:
    """Each tab id resolves to its dedicated screen class, regardless of
    whether the body is still a Phase-3-onward placeholder or the
    real implementation. Bodies that have not yet been filled keep
    rendering a ``<tab>-placeholder`` Static.
    """
    app = TUIApp(client=local_client, is_local=True)
    async with app.run_test() as _pilot:
        assert isinstance(app.query_one("#sessions"), SessionsScreen)
        assert isinstance(app.query_one("#costs"), CostsScreen)
        assert isinstance(app.query_one("#logs"), LogsScreen)
        assert isinstance(app.query_one("#providers"), ProvidersScreen)
        # Phases 3-6 filled in every tab body; no placeholders
        # remain. Each tab now exposes its real header label.
        assert app.query_one("#sessions-header") is not None
        assert app.query_one("#costs-header") is not None
        assert app.query_one("#logs-header") is not None
        assert app.query_one("#providers-header") is not None


# ---------------------------------------------------------------------------
# Vim keybindings
# ---------------------------------------------------------------------------


async def test_q_exits_cleanly(local_client: LocalClient) -> None:
    """Pressing ``q`` exits the app via Textual's built-in quit action.

    The ``run_test`` context-manager exits cleanly when the app
    quits; assert that ``return_code`` is 0 (clean exit) once the
    Pilot loop unwinds.
    """
    app = TUIApp(client=local_client, is_local=False)
    async with app.run_test() as pilot:
        await pilot.press("q")
        await pilot.pause()
    assert app.return_code == 0


async def test_question_mark_pushes_help_overlay(
    local_client: LocalClient,
) -> None:
    """Pressing ``?`` dispatches ``action_help`` which pushes the
    Phase-7 :class:`HelpOverlay` modal onto the screen stack. The
    overlay reads ``BINDINGS`` from the active tab + the App so the
    cheatsheet stays in lockstep with the registered set.
    """
    from voicegateway.cli.tui.screens.help import HelpOverlay

    app = TUIApp(client=local_client, is_local=False)
    async with app.run_test() as pilot:
        await pilot.press("question_mark")
        for _ in range(8):
            await pilot.pause()
        assert any(isinstance(screen, HelpOverlay) for screen in app.screen_stack)
        # Pressing ``?`` again dismisses the modal.
        await pilot.press("question_mark")
        for _ in range(5):
            await pilot.pause()
        assert not any(isinstance(screen, HelpOverlay) for screen in app.screen_stack)


async def test_number_keys_jump_to_each_tab(
    local_client: LocalClient,
) -> None:
    from textual.widgets import ContentSwitcher

    app = TUIApp(client=local_client, is_local=False)
    async with app.run_test() as pilot:
        switcher = app.query_one("#content", ContentSwitcher)
        for key, expected in zip("1234", _TAB_IDS, strict=True):
            await pilot.press(key)
            await pilot.pause()
            assert switcher.current == expected


async def test_tab_cycle_wraps_with_priority_binding(
    local_client: LocalClient,
) -> None:
    from textual.widgets import ContentSwitcher

    app = TUIApp(client=local_client, is_local=False)
    async with app.run_test() as pilot:
        switcher = app.query_one("#content", ContentSwitcher)
        # forward cycle from sessions -> costs
        await pilot.press("tab")
        await pilot.pause()
        assert switcher.current == "costs"
        # shift-tab from sessions wraps to providers
        switcher.current = "sessions"
        await pilot.pause()
        await pilot.press("shift+tab")
        await pilot.pause()
        assert switcher.current == "providers"
        # tab from last wraps back to first
        await pilot.press("tab")
        await pilot.pause()
        assert switcher.current == "sessions"


# ---------------------------------------------------------------------------
# `voicegw tui` daemon-down preflight (no --local)
# ---------------------------------------------------------------------------


def test_voicegw_tui_help_renders() -> None:
    result = runner.invoke(cli_app, ["tui", "--help"])
    assert result.exit_code == 0, result.output
    plain = _plain(result.output)
    for needle in (
        "--local",
        "--url",
        "--token",
        "--history-limit",
        "--theme",
        "--poll",
        "--config",
    ):
        assert needle in plain, f"{needle} missing from --help"


def test_voicegw_tui_prints_daemon_down_message_when_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ``--local``, an unreachable daemon yields a clear
    "Daemon at <url> is not reachable" message and exit code 1
    instead of launching Textual into a hung-poll state.
    """

    def _raise_connect_error(*args: Any, **kwargs: Any) -> Any:
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr("voicegateway.cli.tui.httpx.get", _raise_connect_error)

    result = runner.invoke(cli_app, ["tui", "--url", "http://127.0.0.1:65000"])
    assert result.exit_code == 1, result.output
    plain = _plain(result.output)
    assert "not reachable" in plain
    # Surfaces both fix paths: start the daemon, or fall back to Local mode.
    assert "voicegw start" in plain
    assert "voicegw tui --local" in plain


def test_voicegw_tui_local_skips_preflight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Locked decision 5: ``--local`` always wins regardless of daemon
    state. Even when the preflight would fail, ``--local`` should not
    invoke it -- LocalClient does not need the daemon at all.
    """
    fake_get = MagicMock()
    monkeypatch.setattr("voicegateway.cli.tui.httpx.get", fake_get)
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "voicegw.db"))

    fake_run = MagicMock()
    monkeypatch.setattr(TUIApp, "run", fake_run)

    result = runner.invoke(
        cli_app,
        ["tui", "--local", "--url", "http://unreachable.example"],
    )
    assert result.exit_code == 0, result.output
    assert fake_get.call_count == 0, "preflight must not run with --local"
    assert fake_run.called, "TUIApp.run must still be dispatched"


def test_voicegw_tui_passes_when_health_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: a 200 OK from /health lets the launch proceed."""
    fake_response = MagicMock()
    fake_response.status_code = 200
    monkeypatch.setattr(
        "voicegateway.cli.tui.httpx.get", lambda *a, **kw: fake_response
    )

    fake_run = MagicMock()
    monkeypatch.setattr(TUIApp, "run", fake_run)

    result = runner.invoke(cli_app, ["tui", "--url", "http://daemon.test"])
    assert result.exit_code == 0, result.output
    assert fake_run.called
