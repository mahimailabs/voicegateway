"""Tests for the macOS LaunchAgent backend."""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="MacOS backend uses os.getuid which is not available on Windows",
)


def _ok(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=""
    )


@pytest.fixture
def backend(tmp_path, monkeypatch):
    """A MacOSBackend rooted in tmp_path so every filesystem write"""
    home = tmp_path / "home"
    home.mkdir()
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        "voicegateway.cli.daemon.macos_daemon.user_log_dir",
        lambda *args, **kwargs: str(log_dir),
    )
    monkeypatch.setattr("voicegateway.cli.daemon.macos_daemon.os.getuid", lambda: 501)

    from voicegateway.cli.daemon.macos_daemon import MacOSBackend

    return MacOSBackend()


@pytest.fixture
def fake_launchctl(monkeypatch):
    """Replace subprocess.run with a recording fake."""
    from unittest.mock import MagicMock

    fake = MagicMock(return_value=_ok())
    monkeypatch.setattr("voicegateway.cli.daemon.macos_daemon.subprocess.run", fake)
    return fake


# ---------------------------------------------------------------------------
# render_plist
# ---------------------------------------------------------------------------


def test_render_plist_substitutes_all_variables(backend):
    """The rendered plist parses as valid plist with our values."""
    import plistlib

    rendered = backend._render_plist(executable_path="/usr/local/bin/voicegw")
    parsed = plistlib.loads(rendered.encode())

    assert parsed["Label"] == "ai.openrtc.voicegateway"
    assert parsed["ProgramArguments"] == ["/usr/local/bin/voicegw", "serve"]
    assert parsed["RunAtLoad"] is True
    assert parsed["KeepAlive"] is True
    assert parsed["StandardOutPath"] == str(backend._stdout_log)
    assert parsed["StandardErrorPath"] == str(backend._stderr_log)
    assert parsed["WorkingDirectory"] == str(backend._home)


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------


def test_install_writes_plist_and_bootstraps(backend, fake_launchctl, monkeypatch):
    monkeypatch.setattr(
        "voicegateway.cli.daemon.macos_daemon.shutil.which",
        lambda _: "/usr/local/bin/voicegw",
    )

    backend.install()

    assert backend._plist_path.exists(), "plist should be written"
    body = backend._plist_path.read_text()
    assert "/usr/local/bin/voicegw" in body
    assert backend._service_name in body

    # plist file mode is 0644 (per design.md §Daemon registration rules).
    assert (backend._plist_path.stat().st_mode & 0o777) == 0o644

    bootstrap_calls = [
        c for c in fake_launchctl.call_args_list if "bootstrap" in c.args[0]
    ]
    assert len(bootstrap_calls) == 1
    args = bootstrap_calls[0].args[0]
    assert args[0] == "launchctl"
    assert args[1] == "bootstrap"
    assert args[2] == backend._domain_target
    assert args[3] == str(backend._plist_path)


def test_install_raises_when_voicegw_not_on_path(backend, monkeypatch):
    monkeypatch.setattr("voicegateway.cli.daemon.macos_daemon.shutil.which", lambda _: None)
    with pytest.raises(RuntimeError, match="voicegw"):
        backend.install()


def test_install_swallows_already_loaded_returncode(
    backend, fake_launchctl, monkeypatch
):
    """launchctl bootstrap returns 17 when already loaded; install is idempotent."""
    monkeypatch.setattr(
        "voicegateway.cli.daemon.macos_daemon.shutil.which",
        lambda _: "/usr/local/bin/voicegw",
    )
    fake_launchctl.return_value = _ok(returncode=17)
    # Should NOT raise.
    backend.install()


def test_install_raises_on_other_bootstrap_failure(
    backend, fake_launchctl, monkeypatch
):
    monkeypatch.setattr(
        "voicegateway.cli.daemon.macos_daemon.shutil.which",
        lambda _: "/usr/local/bin/voicegw",
    )
    fake_launchctl.return_value = subprocess.CompletedProcess(
        args=[], returncode=5, stdout="", stderr="permission denied"
    )
    with pytest.raises(RuntimeError, match="permission denied"):
        backend.install()


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------


def test_uninstall_calls_bootout_and_removes_plist(backend, fake_launchctl):
    # Pre-create the plist so we can assert it's removed.
    backend._launch_agents_dir.mkdir(parents=True)
    backend._plist_path.write_text("dummy")

    backend.uninstall()

    assert not backend._plist_path.exists()
    bootout_calls = [c for c in fake_launchctl.call_args_list if "bootout" in c.args[0]]
    assert len(bootout_calls) == 1


def test_uninstall_idempotent_when_not_installed(backend, fake_launchctl):
    """No plist on disk and bootout returns non-zero is fine."""
    fake_launchctl.return_value = _ok(returncode=113)  # not bootstrapped
    backend.uninstall()  # must not raise


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_when_not_registered(backend, fake_launchctl):
    """launchctl print returns non-zero when service isn't bootstrapped."""
    fake_launchctl.return_value = _ok(returncode=113)
    s = backend.status()
    assert s["registered"] is False
    assert s["running"] is False
    assert s["pid"] is None
    assert s["service_name"] == "ai.openrtc.voicegateway"


def test_status_when_running(backend, fake_launchctl):
    fake_launchctl.return_value = _ok(
        stdout=("ai.openrtc.voicegateway = {\n    state = running\n    pid = 12345\n}")
    )
    s = backend.status()
    assert s["registered"] is True
    assert s["running"] is True
    assert s["pid"] == 12345


def test_status_when_registered_but_idle(backend, fake_launchctl):
    """Registered but state isn't running (e.g. waiting on a trigger)."""
    fake_launchctl.return_value = _ok(
        stdout=("ai.openrtc.voicegateway = {\n    state = waiting\n}")
    )
    s = backend.status()
    assert s["registered"] is True
    assert s["running"] is False
    assert s["pid"] is None


# ---------------------------------------------------------------------------
# start / stop / restart
# ---------------------------------------------------------------------------


def test_start_when_not_registered_bootstraps(backend, fake_launchctl):
    """No active registration but plist on disk: bootstrap it."""
    backend._launch_agents_dir.mkdir(parents=True)
    backend._plist_path.write_text("dummy")
    # First call (print) returns failure (not registered);
    # bootstrap returns 0.
    fake_launchctl.side_effect = [_ok(returncode=113), _ok()]

    backend.start()

    args_seen = [c.args[0] for c in fake_launchctl.call_args_list]
    assert any("bootstrap" in a for a in args_seen)


def test_start_when_not_registered_and_no_plist_raises(backend, fake_launchctl):
    fake_launchctl.return_value = _ok(returncode=113)
    with pytest.raises(RuntimeError, match="plist missing"):
        backend.start()


def test_start_when_registered_kickstarts(backend, fake_launchctl):
    fake_launchctl.return_value = _ok(stdout="ai.openrtc.voicegateway = {}")

    backend.start()

    args_seen = [c.args[0] for c in fake_launchctl.call_args_list]
    assert any("kickstart" in a for a in args_seen)


def test_stop_no_op_when_not_registered(backend, fake_launchctl):
    fake_launchctl.return_value = _ok(returncode=113)
    backend.stop()
    # Only the print call; no bootout.
    args_seen = [c.args[0] for c in fake_launchctl.call_args_list]
    assert not any("bootout" in a for a in args_seen)


def test_stop_calls_bootout_when_registered(backend, fake_launchctl):
    fake_launchctl.return_value = _ok(stdout="ai.openrtc.voicegateway = {}")
    backend.stop()
    args_seen = [c.args[0] for c in fake_launchctl.call_args_list]
    assert any("bootout" in a for a in args_seen)


def test_restart_when_registered_uses_kickstart_dash_k(backend, fake_launchctl):
    fake_launchctl.return_value = _ok(stdout="ai.openrtc.voicegateway = {}")
    backend.restart()
    kickstart_calls = [
        c for c in fake_launchctl.call_args_list if "kickstart" in c.args[0]
    ]
    assert kickstart_calls
    # The -k flag must appear in the kickstart command.
    assert any("-k" in c.args[0] for c in kickstart_calls)


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------


def test_logs_returns_empty_when_files_missing(backend):
    assert backend.logs(tail=10) == ""


def test_logs_reads_stdout(backend):
    backend._log_dir.mkdir(parents=True)
    backend._stdout_log.write_text("line1\nline2\nline3\n")
    out = backend.logs(tail=10)
    assert "line1" in out
    assert "line3" in out


def test_logs_appends_stderr_with_separator(backend):
    backend._log_dir.mkdir(parents=True)
    backend._stdout_log.write_text("normal\n")
    backend._stderr_log.write_text("oops\n")
    out = backend.logs(tail=10)
    assert "normal" in out
    assert "oops" in out
    assert "--- stderr ---" in out


def test_logs_respects_tail_parameter(backend):
    backend._log_dir.mkdir(parents=True)
    body = "".join(f"line{i}\n" for i in range(50))
    backend._stdout_log.write_text(body)
    out = backend.logs(tail=3)
    # Last three lines only.
    assert "line47" in out
    assert "line48" in out
    assert "line49" in out
    assert "line0" not in out
    assert "line46" not in out
