"""DaemonManager picks the right OS backend for the running platform."""

from __future__ import annotations

import importlib

import pytest

from voicegateway.cli.daemon import DaemonManager
from voicegateway.cli.daemon.manager import _select_backend_name


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("darwin", "macos"),
        ("linux", "linux"),
        ("linux2", "linux"),  # older Python returned this
        ("win32", "windows"),
        ("freebsd", "linux"),  # any other Unix falls into the linux bucket
    ],
)
def test_select_backend_name_routes_correctly(monkeypatch, platform, expected):
    monkeypatch.setattr("sys.platform", platform)
    assert _select_backend_name() == expected


@pytest.mark.parametrize(
    ("platform", "module", "cls"),
    [
        ("linux", "linux_daemon", "LinuxBackend"),
        ("darwin", "macos_daemon", "MacOSBackend"),
        ("win32", "windows_daemon", "WindowsBackend"),
    ],
)
def test_default_construction_loads_platform_backend(
    monkeypatch, platform, module, cls
):
    monkeypatch.setattr("sys.platform", platform)
    # MacOSBackend calls os.getuid; keep the test independent of the runner's uid.
    monkeypatch.setattr("voicegateway.cli.daemon.macos_daemon.os.getuid", lambda: 501)
    backend_cls = getattr(
        importlib.import_module(f"voicegateway.cli.daemon.{module}"), cls
    )
    assert isinstance(DaemonManager()._backend, backend_cls)
