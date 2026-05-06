"""Active-project resolution for the voicegateway.inference module.

Per design.md section 3.3, the active project is resolved in this order:

1. The project name set via ``inference.set_project(name)`` in the same
   call context.
2. The ``VOICEGW_ACTIVE_PROJECT`` environment variable.
3. The ``default_project`` field in voicegw.yaml.
4. Hard error.

This is a v0.0.5 stub: the full project-aware key resolution lands in
section 5.7 of TODO.md. Right now ``set_project`` and
``VOICEGW_ACTIVE_PROJECT`` are honored, but the YAML ``default_project``
fallback resolves to the literal string ``"default"`` to mirror the
existing Gateway behavior. Section 5.7 will plumb the real YAML field
through and tighten the error case.
"""

from __future__ import annotations

import os
from contextvars import ContextVar

_DEFAULT_PROJECT_NAME = "default"
_ENV_VAR = "VOICEGW_ACTIVE_PROJECT"

_current_project: ContextVar[str | None] = ContextVar(
    "vg_active_project", default=None
)


def set_project(name: str) -> None:
    """Set the active project for the current async context.

    Subsequent ``inference.STT/LLM/TTS`` factories called in the same
    context will look up keys under this project.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("Project name must be a non-empty string")
    _current_project.set(name)


def get_active_project() -> str:
    """Return the active project name following the resolution order.

    Returns the literal ``"default"`` when nothing is configured (matches
    the existing Gateway default project semantics). Section 5.7 will
    replace this fallback with a hard error when no ``default_project``
    is configured in voicegw.yaml.
    """
    explicit = _current_project.get()
    if explicit:
        return explicit

    env_value = os.environ.get(_ENV_VAR)
    if env_value:
        return env_value

    return _DEFAULT_PROJECT_NAME


def reset_project() -> None:
    """Clear the per-context project. Test-only."""
    _current_project.set(None)
