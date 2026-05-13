"""TUI data-layer exceptions."""

from __future__ import annotations


class LocalModeUnsupportedError(Exception):
    """Raised by ``LocalClient`` write methods."""

    def __init__(self, *, feature: str) -> None:
        self.feature = feature
        super().__init__(
            f"This action ({feature}) is unavailable in Local mode. "
            "Start the daemon (`voicegw start`) to enable it."
        )


__all__ = ["LocalModeUnsupportedError"]
