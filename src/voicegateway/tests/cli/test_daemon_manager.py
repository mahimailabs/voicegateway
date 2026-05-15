"""Tests for the DaemonManager facade and its platform selector."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from voicegateway.cli.daemon import DaemonManager
from voicegateway.cli.daemon.manager import _select_backend_name

# ---------------------------------------------------------------------------
# Platform selection.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("darwin", "macos"),
        ("linux", "linux"),
        (
            "linux2",
            "linux",
        ),  # older Python returned this; selector still routes correctly
        ("win32", "windows"),
        (
            "freebsd",
            "linux",
        ),  # any Unix that isn't darwin/win32 falls into the linux bucket
    ],
)
def test_select_backend_name_routes_correctly(monkeypatch, platform, expected):
    """Every supported platform string maps to one of the three backends."""
    monkeypatch.setattr("sys.platform", platform)
    assert _select_backend_name() == expected


# ---------------------------------------------------------------------------
# Facade delegation.
# ---------------------------------------------------------------------------


def test_manager_constructed_with_backend_uses_it() -> None:
    """Passing ``backend=`` skips the real backend import entirely."""
    backend = Mock()
    mgr = DaemonManager(backend=backend)
    # Backend reference is stored as-is.
    assert mgr._backend is backend


def test_install_delegates() -> None:
    backend = Mock()
    DaemonManager(backend=backend).install()
    backend.install.assert_called_once_with()


def test_uninstall_delegates() -> None:
    backend = Mock()
    DaemonManager(backend=backend).uninstall()
    backend.uninstall.assert_called_once_with()


def test_start_delegates() -> None:
    backend = Mock()
    DaemonManager(backend=backend).start()
    backend.start.assert_called_once_with()


def test_stop_delegates() -> None:
    backend = Mock()
    DaemonManager(backend=backend).stop()
    backend.stop.assert_called_once_with()


def test_restart_delegates() -> None:
    backend = Mock()
    DaemonManager(backend=backend).restart()
    backend.restart.assert_called_once_with()


def test_status_returns_backend_payload() -> None:
    """``status()`` is the only method that returns a value."""
    backend = Mock()
    expected = {"running": True, "registered": True, "pid": 1234}
    backend.status.return_value = expected

    out = DaemonManager(backend=backend).status()

    backend.status.assert_called_once_with()
    assert out == expected


def test_logs_forwards_tail_kwarg() -> None:
    """``logs(tail=42)`` reaches the backend with the tail kwarg."""
    backend = Mock()
    backend.logs.return_value = "line1\nline2\nline3"

    out = DaemonManager(backend=backend).logs(tail=42)

    backend.logs.assert_called_once_with(tail=42)
    assert out == "line1\nline2\nline3"


def test_logs_defaults_to_tail_100() -> None:
    backend = Mock()
    backend.logs.return_value = ""

    DaemonManager(backend=backend).logs()

    backend.logs.assert_called_once_with(tail=100)


# ---------------------------------------------------------------------------
# Real-backend lazy import path.
#
# All three OS backends now exist (macOS / Linux / Windows). The
# regression test that used to point at the still-pending backend is
# gone; the three positive construction assertions below cover what
# matters: ``DaemonManager()`` on each platform picks the right
# backend type.
# ---------------------------------------------------------------------------


def test_default_construction_loads_linux_backend_on_linux(monkeypatch) -> None:
    """Positive assertion: on linux the facade picks LinuxBackend."""
    monkeypatch.setattr("sys.platform", "linux")

    from voicegateway.cli.daemon.linux import LinuxBackend

    mgr = DaemonManager()
    assert isinstance(mgr._backend, LinuxBackend)


def test_default_construction_loads_macos_backend_on_darwin(monkeypatch) -> None:
    """Positive assertion: on darwin the facade picks MacOSBackend."""
    monkeypatch.setattr("sys.platform", "darwin")
    # The MacOSBackend constructor calls os.getuid; patch so this
    # test does not depend on the runner's actual uid.
    monkeypatch.setattr("voicegateway.cli.daemon.macos.os.getuid", lambda: 501)

    from voicegateway.cli.daemon.macos import MacOSBackend

    mgr = DaemonManager()
    assert isinstance(mgr._backend, MacOSBackend)


def test_default_construction_loads_windows_backend_on_win32(monkeypatch) -> None:
    """Positive assertion: on win32 the facade picks WindowsBackend."""
    monkeypatch.setattr("sys.platform", "win32")

    from voicegateway.cli.daemon.windows import WindowsBackend

    mgr = DaemonManager()
    assert isinstance(mgr._backend, WindowsBackend)
