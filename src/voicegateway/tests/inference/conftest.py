"""Shared fixtures for the inference test package."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_inference_state():
    """Reset cached singletons + ContextVars between tests.

    The inference module caches a Gateway instance and stores active
    project / session id in ContextVars. Tests must start from a known
    blank state.
    """
    from voicegateway.inference._factory import reset_gateway
    from voicegateway.inference._project import reset_project
    from voicegateway.inference._session_context import reset_session_id

    reset_gateway()
    reset_project()
    reset_session_id()
    yield
    reset_gateway()
    reset_project()
    reset_session_id()
