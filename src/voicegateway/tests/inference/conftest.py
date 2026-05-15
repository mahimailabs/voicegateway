"""Shared fixtures for the inference test package."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_inference_state():
    """Reset cached singletons + ContextVars between tests."""
    from voicegateway.core.gateway_factory import reset_gateway
    from voicegateway.core.active_project import reset_project
    from voicegateway.inference.session.context import reset_session_id

    reset_gateway()
    reset_project()
    reset_session_id()
    yield
    reset_gateway()
    reset_project()
    reset_session_id()
