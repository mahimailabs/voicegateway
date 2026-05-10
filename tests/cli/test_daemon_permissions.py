"""File-permission contract for each daemon backend.

The macOS LaunchAgent plist and the Linux systemd user unit must be
written with mode 0o644 (per design.md daemon-registration rules:
"Validate file permissions after writing"). The Windows backend
writes a Start Menu Startup-folder ``.lnk`` and a Scheduled Task
registration; neither is a POSIX file in the Windows sense and the
Windows backend explicitly does NOT call ``.chmod()`` on anything.

These assertions also exist inline in the per-backend install
tests (``test_install_writes_plist_and_bootstraps`` on macOS,
``test_install_writes_unit_and_runs_systemctl`` on Linux), but
this consolidated file makes the per-platform contract reviewable
in one place.

Skipped on Windows because the macOS-backend test depends on
``os.getuid`` which Windows does not expose; the file-permissions
contract is implicitly verified by the per-backend tests anyway.
"""

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
        "voicegateway.cli.daemon.macos.user_log_dir",
        lambda *args, **kwargs: str(log_dir),
    )
    monkeypatch.setattr("voicegateway.cli.daemon.macos.os.getuid", lambda: 501)
    monkeypatch.setattr(
        "voicegateway.cli.daemon.macos.shutil.which",
        lambda _: "/usr/local/bin/voicegw",
    )
    monkeypatch.setattr(
        "voicegateway.cli.daemon.macos.subprocess.run", MagicMock(return_value=_ok())
    )

    from voicegateway.cli.daemon.macos import MacOSBackend

    return MacOSBackend()


@pytest.fixture
def linux_backend(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        "voicegateway.cli.daemon.linux.shutil.which",
        lambda _: "/usr/local/bin/voicegw",
    )
    monkeypatch.setattr(
        "voicegateway.cli.daemon.linux.subprocess.run", MagicMock(return_value=_ok())
    )

    from voicegateway.cli.daemon.linux import LinuxBackend

    return LinuxBackend()


@pytest.fixture
def windows_backend(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        "voicegateway.cli.daemon.windows.user_log_dir",
        lambda *args, **kwargs: str(log_dir),
    )
    monkeypatch.setattr(
        "voicegateway.cli.daemon.windows.shutil.which",
        lambda name: (
            "C:\\Users\\example\\.local\\bin\\voicegw.exe"
            if name in ("voicegw", "voicegw.exe")
            else None
        ),
    )
    monkeypatch.setattr(
        "voicegateway.cli.daemon.windows.subprocess.run", MagicMock(return_value=_ok())
    )

    from voicegateway.cli.daemon.windows import WindowsBackend

    return WindowsBackend()


# ---------------------------------------------------------------------------
# macOS plist must be 0o644
# ---------------------------------------------------------------------------


def test_macos_plist_written_with_mode_0644(macos_backend):
    """LaunchAgent plists at ~/Library/LaunchAgents/ must be world-readable
    by the user (launchd reads them) and not group/world-writable. 0o644
    is the standard.
    """
    macos_backend.install()
    mode = macos_backend._plist_path.stat().st_mode & 0o777
    assert mode == 0o644, f"plist mode is {oct(mode)}, expected 0o644"


# ---------------------------------------------------------------------------
# Linux systemd unit must be 0o644
# ---------------------------------------------------------------------------


def test_linux_unit_written_with_mode_0644(linux_backend):
    """systemd unit files at ~/.config/systemd/user/ must be readable
    by the systemd-user manager and not group/world-writable. 0o644
    matches what systemctl --user expects.
    """
    linux_backend.install()
    mode = linux_backend._unit_path.stat().st_mode & 0o777
    assert mode == 0o644, f"unit mode is {oct(mode)}, expected 0o644"


# ---------------------------------------------------------------------------
# Windows: no chmod call
# ---------------------------------------------------------------------------


def test_windows_install_does_not_chmod(windows_backend, monkeypatch):
    """The Windows backend must NOT call ``Path.chmod()``: NTFS does
    not use POSIX permissions, and the ``.lnk`` and Scheduled Task
    registration both inherit ACLs from their containing locations.
    A regression that adds chmod here would silently fail on Windows
    (chmod is a no-op there) but would make the macOS / Linux tests
    less informative if the backends share helpers.
    """
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
