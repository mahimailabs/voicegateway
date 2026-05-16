"""Tests for the Windows Scheduled Task backend."""

from __future__ import annotations

import subprocess

import pytest


def _ok(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=""
    )


@pytest.fixture
def backend(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        "voicegateway.cli.daemon.windows_daemon.user_log_dir",
        lambda *args, **kwargs: str(log_dir),
    )

    from voicegateway.cli.daemon.windows_daemon import WindowsBackend

    return WindowsBackend()


@pytest.fixture
def fake_subprocess(monkeypatch):
    from unittest.mock import MagicMock

    fake = MagicMock(return_value=_ok())
    monkeypatch.setattr("voicegateway.cli.daemon.windows_daemon.subprocess.run", fake)
    return fake


def _schtasks_calls(fake) -> list[list[str]]:
    return [c.args[0] for c in fake.call_args_list if c.args[0][0] == "schtasks"]


def _powershell_calls(fake) -> list[list[str]]:
    return [c.args[0] for c in fake.call_args_list if c.args[0][0] == "powershell"]


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------


def test_install_uses_schtasks_first(backend, fake_subprocess, monkeypatch):
    monkeypatch.setattr(
        "voicegateway.cli.daemon.windows_daemon.shutil.which",
        lambda name: (
            "C:\\Users\\example\\.local\\bin\\voicegw.exe"
            if name in ("voicegw", "voicegw.exe")
            else None
        ),
    )

    backend.install()

    schtasks = _schtasks_calls(fake_subprocess)
    assert any("/Create" in c and "/SC" in c and "ONLOGON" in c for c in schtasks)
    # Did NOT fall back to PowerShell; schtasks succeeded.
    assert _powershell_calls(fake_subprocess) == []


def test_install_falls_back_to_startup_shortcut_when_schtasks_fails(
    backend, fake_subprocess, monkeypatch
):
    monkeypatch.setattr(
        "voicegateway.cli.daemon.windows_daemon.shutil.which",
        lambda name: (
            "C:\\Users\\example\\.local\\bin\\voicegw.exe"
            if name in ("voicegw", "voicegw.exe")
            else None
        ),
    )
    # First call (schtasks /Create) fails; second (powershell) succeeds.
    fake_subprocess.side_effect = [_ok(returncode=1, stdout="Access denied"), _ok()]

    backend.install()

    schtasks = _schtasks_calls(fake_subprocess)
    powershell = _powershell_calls(fake_subprocess)
    assert len(schtasks) == 1, "schtasks should have been attempted exactly once"
    assert len(powershell) == 1, "PowerShell fallback should have been called"


def test_install_raises_when_both_paths_fail(backend, fake_subprocess, monkeypatch):
    monkeypatch.setattr(
        "voicegateway.cli.daemon.windows_daemon.shutil.which",
        lambda name: (
            "C:\\Users\\example\\.local\\bin\\voicegw.exe"
            if name in ("voicegw", "voicegw.exe")
            else None
        ),
    )
    fake_subprocess.return_value = _ok(
        returncode=1,
        stdout="",
    )
    fake_subprocess.return_value.stderr = "everything is broken"
    with pytest.raises(RuntimeError, match="schtasks") as excinfo:
        backend.install()
    # Error message names BOTH the schtasks failure and the
    # PowerShell fallback failure so the operator can debug.
    assert "Startup-folder fallback" in str(excinfo.value)


def test_install_raises_when_voicegw_not_on_path(backend, monkeypatch):
    monkeypatch.setattr(
        "voicegateway.cli.daemon.windows_daemon.shutil.which", lambda _: None
    )
    with pytest.raises(RuntimeError, match="voicegw"):
        backend.install()


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------


def test_uninstall_removes_task_and_shortcut(backend, fake_subprocess):
    # Pre-create the shortcut so we can assert it's removed.
    backend._startup_dir.mkdir(parents=True)
    backend._shortcut_path.write_text("lnk")

    backend.uninstall()

    assert not backend._shortcut_path.exists()
    schtasks = _schtasks_calls(fake_subprocess)
    assert any("/Delete" in c for c in schtasks)


def test_uninstall_idempotent_when_nothing_installed(backend, fake_subprocess):
    fake_subprocess.return_value = _ok(returncode=1, stdout="task not found")
    # No shortcut, schtasks returns non-zero. Must not raise.
    backend.uninstall()


# ---------------------------------------------------------------------------
# start / stop / restart
# ---------------------------------------------------------------------------


def test_start_calls_schtasks_run(backend, fake_subprocess):
    backend.start()
    schtasks = _schtasks_calls(fake_subprocess)
    assert any("/Run" in c for c in schtasks)


def test_start_raises_on_schtasks_failure(backend, fake_subprocess):
    fake_subprocess.return_value = _ok(returncode=1, stdout="")
    fake_subprocess.return_value.stderr = "task not found"
    with pytest.raises(RuntimeError, match="schtasks /Run"):
        backend.start()


def test_stop_calls_schtasks_end(backend, fake_subprocess):
    backend.stop()
    schtasks = _schtasks_calls(fake_subprocess)
    assert any("/End" in c for c in schtasks)


def test_stop_swallows_failure(backend, fake_subprocess):
    """schtasks /End returns non-zero when the task is not running;"""
    fake_subprocess.return_value = _ok(returncode=1)
    backend.stop()  # must not raise


def test_restart_stops_then_starts(backend, fake_subprocess):
    backend.restart()
    schtasks = _schtasks_calls(fake_subprocess)
    # /End must precede /Run.
    end_idx = next(i for i, c in enumerate(schtasks) if "/End" in c)
    run_idx = next(i for i, c in enumerate(schtasks) if "/Run" in c)
    assert end_idx < run_idx


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_when_not_registered(backend, fake_subprocess):
    fake_subprocess.return_value = _ok(returncode=1)
    s = backend.status()
    assert s["registered"] is False
    assert s["running"] is False
    assert s["pid"] is None
    assert s["service_name"] == "VoiceGateway"


def test_status_when_running(backend, fake_subprocess):
    fake_subprocess.return_value = _ok(
        stdout=(
            "HostName: COMPUTER\n"
            "TaskName: \\VoiceGateway\n"
            "Next Run Time: 1/1/2030\n"
            "Status: Running\n"
        )
    )
    s = backend.status()
    assert s["registered"] is True
    assert s["running"] is True
    assert s["pid"] is None  # schtasks does not expose the managed PID


def test_status_when_ready_but_not_running(backend, fake_subprocess):
    fake_subprocess.return_value = _ok(
        stdout=("TaskName: \\VoiceGateway\nStatus: Ready\n")
    )
    s = backend.status()
    assert s["registered"] is True
    assert s["running"] is False


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------


def test_logs_returns_empty_when_no_log_file(backend):
    assert backend.logs(tail=10) == ""


def test_logs_reads_serve_log(backend):
    backend._log_dir.mkdir(parents=True)
    backend._stdout_log.write_text("a\nb\nc\n")
    out = backend.logs(tail=10)
    assert "a" in out
    assert "c" in out


def test_logs_respects_tail(backend):
    backend._log_dir.mkdir(parents=True)
    body = "".join(f"line{i}\n" for i in range(50))
    backend._stdout_log.write_text(body)
    out = backend.logs(tail=3)
    assert "line47" in out
    assert "line48" in out
    assert "line49" in out
    assert "line0" not in out
