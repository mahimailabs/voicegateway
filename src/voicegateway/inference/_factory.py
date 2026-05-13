"""Lazy Gateway singleton shared by the voicegateway.inference module.

The factories (`STT`, `LLM`, `TTS`) need a Gateway to resolve provider keys,
register cost-tracking middleware, and own the SQLite connection. Building
a fresh Gateway per factory call would re-read voicegw.yaml every time,
so this module caches a single instance.

Tests can call ``reset_gateway()`` to force re-initialization (e.g. after
mutating the config file or env vars).
"""

from __future__ import annotations

from voicegateway.core.gateway import Gateway

_gateway: Gateway | None = None


def get_gateway() -> Gateway:
    """Return the shared Gateway instance, building it on first call.

    Reads voicegw.yaml from the standard search paths the Gateway
    constructor uses. Environment-variable substitution and SQLite
    initialization happen on first build only.
    """
    global _gateway  # noqa: PLW0603
    if _gateway is None:
        _gateway = Gateway()
    return _gateway


def reset_gateway() -> None:
    """Clear the cached Gateway. Test-only.

    Production code should never call this. Tests use it to pick up new
    voicegw.yaml content or env vars set after the singleton was built.
    """
    global _gateway  # noqa: PLW0603
    _gateway = None
