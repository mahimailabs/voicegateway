"""Active-project resolution for the voicegateway.inference module.

Resolution order:

1. The project name set via ``inference.set_project(name)`` in the same
   call context.
2. The ``VOICEGW_ACTIVE_PROJECT`` environment variable.
3. The ``default_project`` field in voicegw.yaml.
4. The literal ``"default"``. ``Gateway.__init__`` auto-creates a
   project of that id on first run so this fallback is always backed
   by a real row in storage (or an in-memory ``ProjectConfig`` when
   storage is disabled). A user who configures ``projects:`` without
   ``default_project`` still gets the ``"default"`` fallback; their
   per-project keys only kick in when they call ``set_project`` or
   set ``VOICEGW_ACTIVE_PROJECT``.
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

    1. ``inference.set_project(name)`` in the current context.
    2. ``VOICEGW_ACTIVE_PROJECT`` environment variable.
    3. ``default_project`` field in voicegw.yaml.
    4. The literal ``"default"``. ``Gateway.__init__`` auto-creates a
       project of that id, so this fallback is always backed by a
       configured row.
    """
    explicit = _current_project.get()
    if explicit:
        return explicit

    env_value = os.environ.get(_ENV_VAR)
    if env_value:
        return env_value

    # Imported lazily to avoid circular imports — the factory module
    # itself imports from this module, but only at call time.
    from voicegateway.inference._factory import get_gateway

    gateway = get_gateway()
    yaml_default = gateway.config.default_project
    if yaml_default:
        return yaml_default

    return _DEFAULT_PROJECT_NAME


def reset_project() -> None:
    """Clear the per-context project. Test-only."""
    _current_project.set(None)
