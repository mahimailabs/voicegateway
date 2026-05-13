"""Middleware: cost tracking, latency, rate limiting, fallback chains.

Submodules (``voicegateway.middleware.cost_tracker``,
``voicegateway.middleware.instrumented_provider``, etc.) are the
canonical import paths. This package re-exports nothing at the top
level; ``__all__`` is empty by design (REQ-VG-POLISH-006 AC-1).
"""

__all__: list[str] = []
