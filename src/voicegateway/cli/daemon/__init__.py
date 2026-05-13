"""DaemonManager facade and platform-specific backend selection."""

from __future__ import annotations

import sys
from typing import Any, Protocol


class DaemonBackend(Protocol):
    """Common interface every OS-specific daemon backend implements."""

    def install(self) -> None:
        """Register the daemon with the OS service manager."""
        ...

    def uninstall(self) -> None:
        """Remove the registration only."""
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
        """Return the last N log lines (joined by newlines)."""
        ...


def _select_backend_name() -> str:
    """Pick the backend module name for the current platform."""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    # Linux + every WSL flavour.
    return "linux"


class DaemonManager:
    """Facade over the OS-specific daemon backends."""

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
