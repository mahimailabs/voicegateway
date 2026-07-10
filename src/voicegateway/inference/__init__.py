"""VoiceGateway inference subpackage.

Framework-neutral: importing this package does NOT import ``livekit`` or
``pipecat``. The session helpers (``attach``, ``attach_session``,
``start_session``) and project helpers (``get_active_project``,
``set_project``) are all framework-neutral and imported eagerly.
"""

from __future__ import annotations

from voicegateway.core.active_project import get_active_project, set_project
from voicegateway.inference.session.attach import attach, attach_session
from voicegateway.inference.session.context import start_session

__all__ = [
    "attach",
    "attach_session",
    "get_active_project",
    "set_project",
    "start_session",
]
