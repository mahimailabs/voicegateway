"""Lazy Gateway singleton shared by the voicegateway.inference module."""

from __future__ import annotations

from voicegateway.core.gateway import Gateway

_gateway: Gateway | None = None


def get_gateway() -> Gateway:
    """Return the shared Gateway instance, building it on first call."""
    global _gateway  # noqa: PLW0603
    if _gateway is None:
        _gateway = Gateway()
    return _gateway


def reset_gateway() -> None:
    """Clear the cached Gateway. Test-only."""
    global _gateway  # noqa: PLW0603
    _gateway = None
