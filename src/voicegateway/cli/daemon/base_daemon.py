"""Protocol contract every OS-specific daemon backend implements."""

from __future__ import annotations

from typing import Any, Protocol


class DaemonBackend(Protocol):
    """Common interface every OS-specific daemon backend implements."""

    def install(self, config_path: str | None = None) -> None:
        """Register the daemon with the OS service manager.

        ``config_path`` (when given) is threaded into the launch command as
        ``serve -c <config_path>`` so the daemon serves that exact config.
        """
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
        """Return at least: ``running`` (bool), ``registered`` (bool),"""
        ...

    def logs(self, *, tail: int = 100) -> str:
        """Return the last N log lines (joined by newlines)."""
        ...


__all__ = ["DaemonBackend"]
