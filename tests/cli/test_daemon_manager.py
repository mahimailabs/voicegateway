"""Tests for the DaemonManager facade and its platform selector.

These tests exercise the platform-routing logic and the seven-method
delegation contract without requiring any real OS backend (the
backends arrive in subsequent v0.1.0 commits). The strategy is to
inject a ``unittest.mock.Mock`` as the backend so every facade method
can be verified to forward the call exactly once with the expected
arguments.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from voicegateway.cli.daemon import DaemonManager, _select_backend_name

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
    """Every supported platform string maps to one of the three backends.

    WSL is transparent: inside WSL ``sys.platform`` is ``linux``, so
    the linux entry covers it without a special branch.
    """
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
    """``status()`` is the only method that returns a value.

    The facade just forwards the dict; what's inside is the backend's
    contract per the DaemonBackend Protocol.
    """
    backend = Mock()
    expected = {"running": True, "registered": True, "pid": 1234}
    backend.status.return_value = expected

    out = DaemonManager(backend=backend).status()

    backend.status.assert_called_once_with()
    assert out == expected


def test_logs_forwards_tail_kwarg() -> None:
    """``logs(tail=42)`` reaches the backend with the tail kwarg.

    The default (tail=100) and explicit overrides are both contract.
    """
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
# As OS backends land (macos.py first, then linux.py, then windows.py),
# this test points at whichever backend is still pending so the
# selector's platform routing stays gated. The macOS backend exists
# now; the linux backend is the next v0.1.0 commit. When that lands,
# this test re-targets "win32" / windows. When the Windows backend
# lands, the test is removed (the positive-construction assertions
# in each backend's own test file cover the routing).
# ---------------------------------------------------------------------------


def test_default_construction_attempts_platform_specific_import(monkeypatch) -> None:
    """Without a backend arg, ``DaemonManager()`` tries to import the
    selected backend module. The pending backend (currently linux)
    surfaces as ImportError; the error message must name the
    correct platform-specific module so platform-routing regressions
    are visible.
    """
    monkeypatch.setattr("sys.platform", "linux")
    with pytest.raises(ImportError) as excinfo:
        DaemonManager()
    assert "voicegateway.cli.daemon.linux" in str(excinfo.value)


def test_default_construction_loads_macos_backend_on_darwin(monkeypatch) -> None:
    """Positive assertion: on darwin the facade picks MacOSBackend."""
    monkeypatch.setattr("sys.platform", "darwin")
    # The MacOSBackend constructor calls os.getuid; patch so this
    # test does not depend on the runner's actual uid.
    monkeypatch.setattr("voicegateway.cli.daemon.macos.os.getuid", lambda: 501)

    from voicegateway.cli.daemon.macos import MacOSBackend

    mgr = DaemonManager()
    assert isinstance(mgr._backend, MacOSBackend)
