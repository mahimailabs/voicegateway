"""DaemonManager facade and platform-specific backend selection.

The v0.1.0 daemon-first onboarding spec (REQ-VG-ONBOARD-003 plus the
lifecycle requirement REQ-VG-ONBOARD-004) needs the cli to install
and drive a long-lived background gateway through the host's native
service manager:

- macOS:   LaunchAgent plist under ~/Library/LaunchAgents/
- Linux:   systemd --user unit under ~/.config/systemd/user/
- Windows: Scheduled Task via schtasks.exe (best-effort; WSL is the
           officially supported path on Windows)

Each backend implements the same seven methods (the
``DaemonBackend`` Protocol below). ``DaemonManager`` is the facade
the cli's lifecycle commands call into; it picks the right backend
at construction time and forwards every call.

The OS-specific backends land in subsequent v0.1.0 commits
(``macos.py``, ``linux.py``, ``windows.py``). This module is
deliberately self-contained so it can be imported and tested with a
fake backend before any real backend exists.
"""

from __future__ import annotations

import sys
from typing import Any, Protocol


class DaemonBackend(Protocol):
    """Common interface every OS-specific daemon backend implements.

    A backend is a thin wrapper around the host's service manager.
    Methods raise on failure (``RuntimeError`` or
    ``subprocess.CalledProcessError``); callers in the cli surface
    those as plain-language error messages and the appropriate exit
    code (1 user-recoverable, 2 system error).
    """

    def install(self) -> None:
        """Register the daemon with the OS service manager.

        Idempotent: re-installing on a machine that already has the
        registration is a no-op or a refresh, never an error.
        """
        ...

    def uninstall(self) -> None:
        """Remove the registration only.

        Per design.md decision 5, ``voicegw uninstall-daemon``
        preserves the config, the SQLite call DB, and any
        managed_providers rows. Only the OS-level service
        registration goes.
        """
        ...

    def start(self) -> None:
        """Tell the service manager to bring the daemon up."""
        ...

    def stop(self) -> None:
        """Tell the service manager to bring the daemon down."""
        ...

    def restart(self) -> None:
        """Stop + start. Most service managers expose this directly."""
        ...

    def status(self) -> dict[str, Any]:
        """Return at least: ``running`` (bool), ``registered`` (bool),
        ``pid`` (int or None). Backends MAY include extra keys
        (e.g. ``last_exit_code`` on macOS) for richer reporting in
        ``voicegw doctor``.
        """
        ...

    def logs(self, *, tail: int = 100) -> str:
        """Return the last N log lines (joined by newlines).

        Each backend pulls from the OS-native log surface
        (``log show`` on macOS, ``journalctl`` on Linux, the
        Scheduled Task event log on Windows). Implementations are
        expected to bound their query so tail=100 cannot scan the
        entire system log.
        """
        ...


def _select_backend_name() -> str:
    """Pick the backend module name for the current platform.

    Returns one of "macos", "linux", "windows". WSL on Windows is
    transparent here: ``sys.platform`` is "linux" inside WSL, so the
    Linux systemd backend is used. This matches design.md §3 which
    documents WSL as the supported Windows path; bare-Windows
    Scheduled Task is best-effort.
    """
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    # Linux + every WSL flavour.
    return "linux"


class DaemonManager:
    """Facade over the OS-specific daemon backends.

    Construction selects the backend by ``sys.platform``; every
    method delegates to the backend. Tests inject a fake backend
    via the ``backend=`` constructor argument so the facade can be
    exercised without dragging in real subprocess calls.
    """

    def __init__(self, backend: DaemonBackend | None = None) -> None:
        self._backend: DaemonBackend = (
            backend if backend is not None else self._load_backend()
        )

    @staticmethod
    def _load_backend() -> DaemonBackend:
        """Lazy-import the platform backend so unit tests that pass
        an explicit fake backend never touch the real ones.
        """
        name = _select_backend_name()
        # The backends do not exist yet (they land in subsequent
        # v0.1.0 commits), so mypy types these imports as ``Any``.
        # Cast each instance to ``DaemonBackend`` so the static
        # contract is the documented one even before the modules
        # arrive; runtime duck-typing through the Protocol still
        # validates each method when the backends actually ship.
        from typing import cast

        backend: DaemonBackend
        if name == "macos":
            from voicegateway.cli.daemon.macos import MacOSBackend

            backend = cast(DaemonBackend, MacOSBackend())
        elif name == "linux":
            from voicegateway.cli.daemon.linux import LinuxBackend

            backend = cast(DaemonBackend, LinuxBackend())
        elif name == "windows":
            from voicegateway.cli.daemon.windows import WindowsBackend

            backend = cast(DaemonBackend, WindowsBackend())
        else:
            raise NotImplementedError(f"Unknown backend selector: {name!r}")
        return backend

    # ---- Pass-throughs ----------------------------------------------------

    def install(self) -> None:
        self._backend.install()

    def uninstall(self) -> None:
        self._backend.uninstall()

    def start(self) -> None:
        self._backend.start()

    def stop(self) -> None:
        self._backend.stop()

    def restart(self) -> None:
        self._backend.restart()

    def status(self) -> dict[str, Any]:
        return self._backend.status()

    def logs(self, *, tail: int = 100) -> str:
        return self._backend.logs(tail=tail)


__all__ = ["DaemonBackend", "DaemonManager"]
