"""Tests for the Linux systemd user-unit backend.

Strategy mirrors test_daemon_macos.py: every subprocess call goes
through one seam so tests monkeypatch the module-level
``subprocess.run`` to capture args and return canned output. HOME
points at tmp_path so writes are sandboxed. The integration test
that drives a real ``systemctl --user`` lives in the Docker matrix
workflow (deferred follow-up); these tests cover the unit-level
contract.
"""

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
    monkeypatch.setenv("HOME", str(home))

    from voicegateway.cli.daemon.linux import LinuxBackend

    return LinuxBackend()


@pytest.fixture
def fake_subprocess(monkeypatch):
    from unittest.mock import MagicMock

    fake = MagicMock(return_value=_ok())
    monkeypatch.setattr("voicegateway.cli.daemon.linux.subprocess.run", fake)
    return fake


def _systemctl_calls(fake) -> list[list[str]]:
    return [c.args[0] for c in fake.call_args_list if c.args[0][0] == "systemctl"]


def _journalctl_calls(fake) -> list[list[str]]:
    return [c.args[0] for c in fake.call_args_list if c.args[0][0] == "journalctl"]


# ---------------------------------------------------------------------------
# render_unit
# ---------------------------------------------------------------------------


def test_render_unit_substitutes_all_variables(backend):
    """The rendered unit parses through configparser and resolves
    every promised key.
    """
    import configparser

    rendered = backend._render_unit(executable_path="/usr/local/bin/voicegw")

    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.read_string(rendered)
    assert cp.sections() == ["Unit", "Service", "Install"]
    assert cp.get("Service", "ExecStart") == "/usr/local/bin/voicegw serve"
    assert cp.get("Service", "WorkingDirectory") == str(backend._home)
    assert cp.get("Service", "Restart") == "on-failure"
    assert cp.get("Install", "WantedBy") == "default.target"


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------


def test_install_writes_unit_and_runs_systemctl(backend, fake_subprocess, monkeypatch):
    monkeypatch.setattr(
        "voicegateway.cli.daemon.linux.shutil.which",
        lambda _: "/usr/local/bin/voicegw",
    )

    backend.install()

    assert backend._unit_path.exists()
    body = backend._unit_path.read_text()
    assert "/usr/local/bin/voicegw" in body
    assert (backend._unit_path.stat().st_mode & 0o777) == 0o644

    systemctl = _systemctl_calls(fake_subprocess)
    # daemon-reload + enable --now in that order.
    assert any("daemon-reload" in c for c in systemctl)
    assert any("enable" in c and "--now" in c for c in systemctl)


def test_install_raises_when_voicegw_not_on_path(backend, monkeypatch):
    monkeypatch.setattr("voicegateway.cli.daemon.linux.shutil.which", lambda _: None)
    with pytest.raises(RuntimeError, match="voicegw"):
        backend.install()


def test_install_raises_when_systemctl_fails(backend, fake_subprocess, monkeypatch):
    monkeypatch.setattr(
        "voicegateway.cli.daemon.linux.shutil.which",
        lambda _: "/usr/local/bin/voicegw",
    )
    fake_subprocess.return_value = _ok(returncode=1)
    with pytest.raises(RuntimeError, match="systemctl"):
        backend.install()


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------


def test_uninstall_disables_and_removes_unit(backend, fake_subprocess):
    backend._unit_dir.mkdir(parents=True)
    backend._unit_path.write_text("dummy")

    backend.uninstall()

    assert not backend._unit_path.exists()
    systemctl = _systemctl_calls(fake_subprocess)
    assert any("disable" in c and "--now" in c for c in systemctl)
    assert any("daemon-reload" in c for c in systemctl)


def test_uninstall_idempotent_when_not_installed(backend, fake_subprocess):
    fake_subprocess.return_value = _ok(returncode=1)
    # No unit file, systemctl returns non-zero. Must not raise.
    backend.uninstall()


# ---------------------------------------------------------------------------
# start / stop / restart
# ---------------------------------------------------------------------------


def test_start_calls_systemctl_start(backend, fake_subprocess):
    backend._unit_dir.mkdir(parents=True)
    backend._unit_path.write_text("dummy")

    backend.start()

    systemctl = _systemctl_calls(fake_subprocess)
    assert any("start" in c and "voicegateway.service" in c for c in systemctl)


def test_start_raises_when_unit_missing(backend, fake_subprocess):
    with pytest.raises(RuntimeError, match="Unit file missing"):
        backend.start()


def test_stop_no_op_when_unit_missing(backend, fake_subprocess):
    backend.stop()
    # No systemctl call.
    assert _systemctl_calls(fake_subprocess) == []


def test_stop_calls_systemctl_stop_when_unit_present(backend, fake_subprocess):
    backend._unit_dir.mkdir(parents=True)
    backend._unit_path.write_text("dummy")

    backend.stop()

    systemctl = _systemctl_calls(fake_subprocess)
    assert any("stop" in c for c in systemctl)


def test_restart_calls_systemctl_restart(backend, fake_subprocess):
    backend._unit_dir.mkdir(parents=True)
    backend._unit_path.write_text("dummy")

    backend.restart()

    systemctl = _systemctl_calls(fake_subprocess)
    assert any("restart" in c for c in systemctl)


def test_restart_raises_when_unit_missing(backend, fake_subprocess):
    with pytest.raises(RuntimeError, match="Unit file missing"):
        backend.restart()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_when_running(backend, fake_subprocess):
    backend._unit_dir.mkdir(parents=True)
    backend._unit_path.write_text("dummy")
    fake_subprocess.return_value = _ok(
        stdout=(
            "LoadState=loaded\n"
            "ActiveState=active\n"
            "MainPID=12345\n"
            "UnitFileState=enabled\n"
        )
    )
    s = backend.status()
    assert s["registered"] is True
    assert s["running"] is True
    assert s["pid"] == 12345
    assert s["service_name"] == "voicegateway"


def test_status_when_inactive(backend, fake_subprocess):
    backend._unit_dir.mkdir(parents=True)
    backend._unit_path.write_text("dummy")
    fake_subprocess.return_value = _ok(
        stdout=("LoadState=loaded\nActiveState=inactive\nMainPID=0\n")
    )
    s = backend.status()
    assert s["registered"] is True
    assert s["running"] is False
    assert s["pid"] is None


def test_status_when_unit_missing(backend, fake_subprocess):
    """No unit file on disk → registered=False even if systemctl
    reports 'loaded' (which it should not, but defensive).
    """
    fake_subprocess.return_value = _ok(
        stdout="LoadState=not-found\nActiveState=inactive\nMainPID=0\n"
    )
    s = backend.status()
    assert s["registered"] is False
    assert s["running"] is False
    assert s["pid"] is None


def test_status_when_systemctl_unavailable(backend, fake_subprocess):
    """systemctl returns non-zero → status reports a sensible empty shape."""
    fake_subprocess.return_value = _ok(returncode=1)
    s = backend.status()
    assert s["registered"] is False
    assert s["running"] is False
    assert s["pid"] is None


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------


def test_logs_calls_journalctl_with_tail(backend, fake_subprocess):
    fake_subprocess.return_value = _ok(stdout="line1\nline2\nline3\n")
    out = backend.logs(tail=42)
    assert out == "line1\nline2\nline3\n"
    journalctl = _journalctl_calls(fake_subprocess)
    assert len(journalctl) == 1
    args = journalctl[0]
    assert args[0] == "journalctl"
    assert "--user-unit" in args
    assert "voicegateway.service" in args
    assert "-n" in args
    assert "42" in args


def test_logs_returns_empty_on_journalctl_failure(backend, fake_subprocess):
    """journalctl exits non-zero when there's no journal access; we
    return empty rather than raising.
    """
    fake_subprocess.return_value = _ok(returncode=1)
    assert backend.logs(tail=10) == ""


def test_logs_default_tail_is_100(backend, fake_subprocess):
    fake_subprocess.return_value = _ok(stdout="")
    backend.logs()
    args = _journalctl_calls(fake_subprocess)[0]
    assert "100" in args
