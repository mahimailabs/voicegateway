"""Active-project resolution: ContextVar + env + YAML default fallback chain."""

from __future__ import annotations

import os
from contextvars import ContextVar

from voicegateway.core.gateway_factory import get_gateway

_DEFAULT_PROJECT_NAME = "default"
_ENV_VAR = "VOICEGW_ACTIVE_PROJECT"

_current_project: ContextVar[str | None] = ContextVar("vg_active_project", default=None)


def set_project(name: str) -> None:
    """Set the active project for the current async context."""
    if not isinstance(name, str) or not name:
        raise ValueError("Project name must be a non-empty string")
    _current_project.set(name)


def get_active_project() -> str:
    """Return the active project name following the resolution order."""
    explicit = _current_project.get()
    if explicit:
        return explicit

    env_value = os.environ.get(_ENV_VAR)
    if env_value:
        return env_value

    gateway = get_gateway()
    yaml_default = gateway.config.default_project
    if yaml_default:
        return yaml_default

    return _DEFAULT_PROJECT_NAME


def reset_project() -> None:
    """Clear the per-context project. Test-only."""
    _current_project.set(None)
