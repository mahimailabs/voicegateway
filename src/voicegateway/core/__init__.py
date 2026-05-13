"""Core orchestration: gateway, config, router, registry, model parsing.

Submodules (``voicegateway.core.gateway``, ``voicegateway.core.config``,
``voicegateway.core.router``, etc.) are the canonical import paths.
This package re-exports nothing at the top level, so ``__all__`` is
empty by design (REQ-VG-POLISH-006 AC-1: the public surface is named
explicitly).
"""

__all__: list[str] = []
