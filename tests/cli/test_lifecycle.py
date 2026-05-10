"""Tests for voicegw start / stop / restart.

Each command delegates to ``DaemonManager``. Tests inject a fake
manager via monkeypatch and assert the right method was called +
the right console line was printed. RuntimeError handling is the
documented failure path that bubbles up as exit code 1.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from typer.testing import CliRunner

from voicegateway.cli import app

runner = CliRunner()


def _patch_manager(monkeypatch, fake):
    """Replace DaemonManager so tests can inject a Mock backend."""
    monkeypatch.setattr(
        "voicegateway.cli.daemon.DaemonManager",
        MagicMock(return_value=fake),
    )


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


def test_start_invokes_manager_start(monkeypatch):
    fake = MagicMock()
    _patch_manager(monkeypatch, fake)

    result = runner.invoke(app, ["start"])
    assert result.exit_code == 0, result.output
    fake.start.assert_called_once_with()
    assert "started" in result.output.lower()


def test_start_reports_runtime_error_with_exit_1(monkeypatch):
    fake = MagicMock()
    fake.start.side_effect = RuntimeError("launchctl failed: 5")
    _patch_manager(monkeypatch, fake)

    result = runner.invoke(app, ["start"])
    assert result.exit_code == 1
    assert "Failed to start" in result.output
    assert "launchctl failed: 5" in result.output


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


def test_stop_invokes_manager_stop(monkeypatch):
    fake = MagicMock()
    _patch_manager(monkeypatch, fake)

    result = runner.invoke(app, ["stop"])
    assert result.exit_code == 0, result.output
    fake.stop.assert_called_once_with()
    assert "stopped" in result.output.lower()


def test_stop_reports_runtime_error_with_exit_1(monkeypatch):
    fake = MagicMock()
    fake.stop.side_effect = RuntimeError("systemctl: unit not found")
    _patch_manager(monkeypatch, fake)

    result = runner.invoke(app, ["stop"])
    assert result.exit_code == 1
    assert "Failed to stop" in result.output


# ---------------------------------------------------------------------------
# restart
# ---------------------------------------------------------------------------


def test_restart_invokes_manager_restart(monkeypatch):
    fake = MagicMock()
    _patch_manager(monkeypatch, fake)

    result = runner.invoke(app, ["restart"])
    assert result.exit_code == 0, result.output
    fake.restart.assert_called_once_with()
    assert "restarted" in result.output.lower()


def test_restart_reports_runtime_error_with_exit_1(monkeypatch):
    fake = MagicMock()
    fake.restart.side_effect = RuntimeError("schtasks: permission denied")
    _patch_manager(monkeypatch, fake)

    result = runner.invoke(app, ["restart"])
    assert result.exit_code == 1
    assert "Failed to restart" in result.output


# ---------------------------------------------------------------------------
# Help renders for each command
# ---------------------------------------------------------------------------


def test_start_help_renders():
    result = runner.invoke(app, ["start", "--help"])
    assert result.exit_code == 0
    assert "Bring the background daemon up" in result.output


def test_stop_help_renders():
    result = runner.invoke(app, ["stop", "--help"])
    assert result.exit_code == 0
    assert "Bring the background daemon down" in result.output


def test_restart_help_renders():
    result = runner.invoke(app, ["restart", "--help"])
    assert result.exit_code == 0
    assert "Restart the background daemon" in result.output
