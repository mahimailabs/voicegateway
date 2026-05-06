"""Active-project resolution for the voicegateway.inference module.

Per design.md section 3.3, the active project is resolved in this order:

1. The project name set via ``inference.set_project(name)`` in the same
   call context.
2. The ``VOICEGW_ACTIVE_PROJECT`` environment variable.
3. The ``default_project`` field in voicegw.yaml.
4. Hard error when projects are configured but none was selected.
   Soft fallback to ``"default"`` only when voicegw.yaml has no
   projects configured at all (preserves backward compat for
   pre-v0.0.5 configs that never adopted the projects:/default_project:
   shape).
"""

from __future__ import annotations

import os
from contextvars import ContextVar

from voicegateway.core.config import ConfigError

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

    Resolution order matches design.md section 3.3:

    1. ``inference.set_project(name)`` in the current context.
    2. ``VOICEGW_ACTIVE_PROJECT`` environment variable.
    3. ``default_project`` field in voicegw.yaml.
    4. Hard error if voicegw.yaml has projects configured but none of
       the three above resolved. Soft fallback to ``"default"`` only
       when voicegw.yaml contains zero projects (legacy configs).

    Raises:
        ConfigError: If projects are configured in voicegw.yaml but
            none was selected by any of the resolution rules.
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

    if not gateway.config.projects:
        # No projects configured at all → preserve the legacy
        # gw.stt/llm/tts default-project semantics. The user has not
        # adopted the projects:/default_project: shape yet, so naming
        # a "default" project doesn't surprise them.
        return _DEFAULT_PROJECT_NAME

    raise ConfigError(
        "No active project. Voicegw.yaml has "
        f"{len(gateway.config.projects)} project(s) configured but no "
        "default_project is set. Either set ``default_project: <name>`` "
        "in voicegw.yaml, set the ``VOICEGW_ACTIVE_PROJECT`` env var, "
        "or call ``voicegateway.inference.set_project(name)`` before "
        "constructing inference.STT / LLM / TTS."
    )


def reset_project() -> None:
    """Clear the per-context project. Test-only."""
    _current_project.set(None)
