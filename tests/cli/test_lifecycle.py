"""Tests for voicegw start / stop / restart.

Each command delegates to ``DaemonManager``. Tests inject a fake
manager via monkeypatch and assert the right method was called +
the right console line was printed. RuntimeError handling is the
documented failure path that bubbles up as exit code 1.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import click
import pytest
from typer.testing import CliRunner

from voicegateway.cli import app

runner = CliRunner()


def _plain(output: str) -> str:
    """Strip ANSI escape codes from CliRunner output.

    Typer's Rich help renderer splits option flags across separate
    bold spans (``\\x1b[1m-\\x1b[0m\\x1b[1m-tail\\x1b[0m``) when
    GitHub Actions sets ``FORCE_COLOR=1``, breaking literal substring
    matches against ``--tail`` etc. ``click.unstyle`` is the canonical
    way to normalise output before substring assertions.
    """
    return click.unstyle(output)


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


# ---------------------------------------------------------------------------
# uninstall-daemon - preserved-state contract per design decision 5
# ---------------------------------------------------------------------------


def test_uninstall_daemon_invokes_manager_uninstall(monkeypatch):
    fake = MagicMock()
    _patch_manager(monkeypatch, fake)

    result = runner.invoke(app, ["uninstall-daemon"])
    assert result.exit_code == 0, result.output
    fake.uninstall.assert_called_once_with()


def test_uninstall_daemon_lists_preserved_state(monkeypatch, tmp_path):
    """Output names every preserved artefact (config, call DB,
    managed provider keys) so the user knows nothing else was
    touched. Decision 5 requirement.
    """
    fake = MagicMock()
    _patch_manager(monkeypatch, fake)
    monkeypatch.setenv("HOME", str(tmp_path))

    result = runner.invoke(app, ["uninstall-daemon"])
    assert result.exit_code == 0, result.output
    out = result.output

    assert "registration removed" in out.lower()
    assert "Preserved" in out
    assert "voicegw.yaml" in out
    assert "voicegw.db" in out
    assert "managed_providers" in out


def test_uninstall_daemon_documents_manual_purge_command(monkeypatch, tmp_path):
    """Output includes the documented `rm -rf <config_path>` so the
    user has a one-line cleanup. `voicegw purge-data` is mentioned
    as deferred.
    """
    fake = MagicMock()
    _patch_manager(monkeypatch, fake)
    monkeypatch.setenv("HOME", str(tmp_path))

    result = runner.invoke(app, ["uninstall-daemon"])
    out = result.output
    expected_path = str(tmp_path / ".config" / "voicegateway")
    assert f"rm -rf {expected_path}" in out
    assert "voicegw purge-data" in out


def test_uninstall_daemon_reports_runtime_error_with_exit_1(monkeypatch):
    fake = MagicMock()
    fake.uninstall.side_effect = RuntimeError("launchctl bootout: 5")
    _patch_manager(monkeypatch, fake)

    result = runner.invoke(app, ["uninstall-daemon"])
    assert result.exit_code == 1
    assert "Failed to uninstall" in result.output


def test_uninstall_daemon_help_renders():
    result = runner.invoke(app, ["uninstall-daemon", "--help"])
    assert result.exit_code == 0
    assert "Remove the daemon registration only" in result.output


# ---------------------------------------------------------------------------
# daemon-logs - REQ-004 daemon-log surface
# ---------------------------------------------------------------------------


def test_daemon_logs_calls_manager_logs_with_default_tail(monkeypatch):
    fake = MagicMock()
    fake.logs.return_value = "line1\nline2\nline3\n"
    _patch_manager(monkeypatch, fake)

    result = runner.invoke(app, ["daemon-logs"])
    assert result.exit_code == 0, result.output
    fake.logs.assert_called_once_with(tail=100)
    assert "line1" in result.output
    assert "line3" in result.output


def test_daemon_logs_respects_tail_option(monkeypatch):
    fake = MagicMock()
    fake.logs.return_value = "single\n"
    _patch_manager(monkeypatch, fake)

    result = runner.invoke(app, ["daemon-logs", "--tail", "42"])
    assert result.exit_code == 0, result.output
    fake.logs.assert_called_once_with(tail=42)


def test_daemon_logs_short_flag_works(monkeypatch):
    """`-n` is the standard tail short flag (matches v0.0.5's logs)."""
    fake = MagicMock()
    fake.logs.return_value = "ok\n"
    _patch_manager(monkeypatch, fake)

    result = runner.invoke(app, ["daemon-logs", "-n", "5"])
    assert result.exit_code == 0
    fake.logs.assert_called_once_with(tail=5)


def test_daemon_logs_empty_output_prints_hint(monkeypatch):
    """Empty output is the common case on a fresh install before
    the daemon has emitted anything; print a hint rather than a
    blank screen.
    """
    fake = MagicMock()
    fake.logs.return_value = ""
    _patch_manager(monkeypatch, fake)

    result = runner.invoke(app, ["daemon-logs"])
    assert result.exit_code == 0, result.output
    assert "No daemon logs yet" in result.output
    assert "voicegw start" in result.output


def test_daemon_logs_runtime_error_exits_1(monkeypatch):
    fake = MagicMock()
    fake.logs.side_effect = RuntimeError("journalctl: no journal access")
    _patch_manager(monkeypatch, fake)

    result = runner.invoke(app, ["daemon-logs"])
    assert result.exit_code == 1
    assert "Failed to read daemon logs" in result.output
    assert "journalctl: no journal access" in result.output


def test_daemon_logs_help_renders():
    result = runner.invoke(app, ["daemon-logs", "--help"])
    assert result.exit_code == 0
    plain = _plain(result.output)
    assert "Tail the OS-native daemon log stream" in plain
    assert "--tail" in plain


# ---------------------------------------------------------------------------
# AC-VG-ONBOARD-004.2: every lifecycle command completes within 10s.
#
# The wall-clock budget in the spec covers a real subprocess round-trip
# against the OS service manager. Unit-test budget is much tighter: with
# DaemonManager mocked the only cost is Python+Typer dispatch + a couple
# of console.prints. 1.0s gives plenty of headroom on slow CI runners
# while still catching a regression that adds a sleep, blocking init,
# or accidental network call to the cli surface.
# ---------------------------------------------------------------------------


_CLI_OVERHEAD_BUDGET_S = 1.0


@pytest.mark.parametrize(
    "command",
    [
        ["start"],
        ["stop"],
        ["restart"],
        ["uninstall-daemon"],
    ],
)
def test_lifecycle_command_completes_within_budget(command, monkeypatch, tmp_path):
    """Every lifecycle command must complete within ``_CLI_OVERHEAD_BUDGET_S``
    seconds when DaemonManager is mocked. A regression that adds a
    slow init, a sleep, or an accidental network call trips this gate.

    The 10-second budget from AC-VG-ONBOARD-004.2 covers the real
    subprocess round-trip; this test keeps the CLI surface itself
    well under that threshold so the OS time is the only variable
    in production runs.
    """
    import time

    fake = MagicMock()
    _patch_manager(monkeypatch, fake)
    monkeypatch.setenv("HOME", str(tmp_path))

    start_t = time.perf_counter()
    result = runner.invoke(app, command)
    elapsed = time.perf_counter() - start_t

    assert result.exit_code == 0, result.output
    assert elapsed < _CLI_OVERHEAD_BUDGET_S, (
        f"`voicegw {' '.join(command)}` took {elapsed:.2f}s with a mocked "
        f"DaemonManager (budget {_CLI_OVERHEAD_BUDGET_S}s). "
        "Look for a slow import, sleep, or blocking init."
    )
