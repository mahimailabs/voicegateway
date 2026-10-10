"""voicegw start / stop / restart / uninstall-daemon / daemon-logs."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from voicegateway.cli import app

runner = CliRunner()


def _patch_manager(monkeypatch) -> MagicMock:
    """Replace DaemonManager so tests can inject a Mock backend."""
    fake = MagicMock()
    monkeypatch.setattr(
        "voicegateway.cli.daemon.DaemonManager", MagicMock(return_value=fake)
    )
    return fake


@pytest.mark.parametrize(
    ("command", "method", "done", "failed"),
    [
        ("start", "start", "started", "Failed to start"),
        ("stop", "stop", "stopped", "Failed to stop"),
        ("restart", "restart", "restarted", "Failed to restart"),
        (
            "uninstall-daemon",
            "uninstall",
            "registration removed",
            "Failed to uninstall",
        ),
    ],
)
def test_lifecycle_command(command, method, done, failed, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    fake = _patch_manager(monkeypatch)

    result = runner.invoke(app, [command])
    assert result.exit_code == 0, result.output
    getattr(fake, method).assert_called_once_with()
    assert done in result.output.lower()

    getattr(fake, method).side_effect = RuntimeError("backend said: 5")
    result = runner.invoke(app, [command])
    assert result.exit_code == 1
    assert failed in result.output
    assert "backend said: 5" in result.output


def test_uninstall_daemon_lists_preserved_state_and_purge_command(
    monkeypatch, tmp_path
):
    """Uninstall removes only the registration and says what it left behind."""
    _patch_manager(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))

    out = runner.invoke(app, ["uninstall-daemon"]).output
    assert "Preserved" in out
    assert "voicegw.yaml" in out
    assert "voicegw.db" in out
    assert "managed_providers" in out
    assert f"rm -rf {tmp_path / '.config' / 'voicegateway'}" in out
    assert "voicegw purge-data" in out


@pytest.mark.parametrize(
    ("args", "tail"),
    [([], 100), (["--tail", "42"], 42), (["-n", "5"], 5)],
)
def test_daemon_logs_tail(args, tail, monkeypatch):
    fake = _patch_manager(monkeypatch)
    fake.logs.return_value = "line1\nline3\n"

    result = runner.invoke(app, ["daemon-logs", *args])
    assert result.exit_code == 0, result.output
    fake.logs.assert_called_once_with(tail=tail)
    assert "line1" in result.output and "line3" in result.output


def test_daemon_logs_empty_output_prints_hint(monkeypatch):
    """Empty output is the common case on a fresh install before the first start."""
    fake = _patch_manager(monkeypatch)
    fake.logs.return_value = ""

    result = runner.invoke(app, ["daemon-logs"])
    assert result.exit_code == 0, result.output
    assert "No daemon logs yet" in result.output
    assert "voicegw start" in result.output


def test_daemon_logs_runtime_error_exits_1(monkeypatch):
    fake = _patch_manager(monkeypatch)
    fake.logs.side_effect = RuntimeError("journalctl: no journal access")

    result = runner.invoke(app, ["daemon-logs"])
    assert result.exit_code == 1
    assert "Failed to read daemon logs" in result.output
    assert "journalctl: no journal access" in result.output
