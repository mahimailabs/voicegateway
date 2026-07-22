"""DaemonManager facade + platform-backend selection."""

from __future__ import annotations

import sys
from typing import Any

from voicegateway.cli.daemon.base_daemon import DaemonBackend


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
        an explicit ``backend=`` to the manager do not drag the
        OS-specific module into ``sys.modules``."""
        name = _select_backend_name()

        from typing import cast

        backend: DaemonBackend
        if name == "macos":
            from voicegateway.cli.daemon.macos_daemon import MacOSBackend

            backend = cast(DaemonBackend, MacOSBackend())
        elif name == "linux":
            from voicegateway.cli.daemon.linux_daemon import LinuxBackend

            backend = cast(DaemonBackend, LinuxBackend())
        elif name == "windows":
            from voicegateway.cli.daemon.windows_daemon import WindowsBackend

            backend = cast(DaemonBackend, WindowsBackend())
        else:
            raise NotImplementedError(f"Unknown backend selector: {name!r}")
        return backend

    def install(self, config_path: str | None = None) -> None:
        self._backend.install(config_path=config_path)

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


__all__ = ["DaemonManager"]
