"""File-permission contract for each daemon backend."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock

import pytest


def _ok(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=""
    )


pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="MacOS backend uses os.getuid which is not available on Windows",
)


@pytest.fixture
def macos_backend(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        "voicegateway.cli.daemon.macos_daemon.user_log_dir",
        lambda *args, **kwargs: str(log_dir),
    )
    monkeypatch.setattr("voicegateway.cli.daemon.macos_daemon.os.getuid", lambda: 501)
    monkeypatch.setattr(
        "voicegateway.cli.daemon.macos_daemon.shutil.which",
        lambda _: "/usr/local/bin/voicegw",
    )
    monkeypatch.setattr(
        "voicegateway.cli.daemon.macos_daemon.subprocess.run", MagicMock(return_value=_ok())
    )

    from voicegateway.cli.daemon.macos_daemon import MacOSBackend

    return MacOSBackend()


@pytest.fixture
def linux_backend(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        "voicegateway.cli.daemon.linux_daemon.shutil.which",
        lambda _: "/usr/local/bin/voicegw",
    )
    monkeypatch.setattr(
        "voicegateway.cli.daemon.linux_daemon.subprocess.run", MagicMock(return_value=_ok())
    )

    from voicegateway.cli.daemon.linux_daemon import LinuxBackend

    return LinuxBackend()


@pytest.fixture
def windows_backend(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        "voicegateway.cli.daemon.windows_daemon.user_log_dir",
        lambda *args, **kwargs: str(log_dir),
    )
    monkeypatch.setattr(
        "voicegateway.cli.daemon.windows_daemon.shutil.which",
        lambda name: (
            "C:\\Users\\example\\.local\\bin\\voicegw.exe"
            if name in ("voicegw", "voicegw.exe")
            else None
        ),
    )
    monkeypatch.setattr(
        "voicegateway.cli.daemon.windows_daemon.subprocess.run", MagicMock(return_value=_ok())
    )

    from voicegateway.cli.daemon.windows_daemon import WindowsBackend

    return WindowsBackend()


# ---------------------------------------------------------------------------
# macOS plist must be 0o644
# ---------------------------------------------------------------------------


def test_macos_plist_written_with_mode_0644(macos_backend):
    """LaunchAgent plists at ~/Library/LaunchAgents/ must be world-readable"""
    macos_backend.install()
    mode = macos_backend._plist_path.stat().st_mode & 0o777
    assert mode == 0o644, f"plist mode is {oct(mode)}, expected 0o644"


# ---------------------------------------------------------------------------
# Linux systemd unit must be 0o644
# ---------------------------------------------------------------------------


def test_linux_unit_written_with_mode_0644(linux_backend):
    """systemd unit files at ~/.config/systemd/user/ must be readable"""
    linux_backend.install()
    mode = linux_backend._unit_path.stat().st_mode & 0o777
    assert mode == 0o644, f"unit mode is {oct(mode)}, expected 0o644"


# ---------------------------------------------------------------------------
# Windows: no chmod call
# ---------------------------------------------------------------------------


def test_windows_install_does_not_chmod(windows_backend, monkeypatch):
    """The Windows backend must NOT call ``Path.chmod()``: NTFS does"""
    from pathlib import Path

    chmod_calls: list[tuple[Path, int]] = []
    real_chmod = Path.chmod

    def recording_chmod(self: Path, mode: int) -> None:
        chmod_calls.append((self, mode))
        real_chmod(self, mode)

    monkeypatch.setattr(Path, "chmod", recording_chmod)

    windows_backend.install()

    assert chmod_calls == [], (
        f"WindowsBackend.install() called Path.chmod() on {chmod_calls}; "
        "Windows NTFS has no POSIX permissions and the install path "
        "should not pretend otherwise."
    )
