"""Drop-in mirror of `livekit.agents.inference` backed by VoiceGateway.

Users replace::

    from livekit.agents import inference

with::

    from voicegateway import inference

and keep the rest of their agent code identical. Each factory
constructs the underlying ``livekit.plugins.<provider>`` instance with
the project's configured key, then wraps it in an
``Instrumented{STT,LLM,TTS}`` so cost, latency, and session correlation
are recorded transparently.

Public symbols mirror ``livekit.agents.inference`` constructor signatures
exactly so a one-line import swap is enough; the drop-in compat test
in ``tests/inference/test_drop_in_compatibility.py`` is the success gate.
"""

from voicegateway.inference._llm import LLM
from voicegateway.inference._project import get_active_project, set_project
from voicegateway.inference._session_attach import attach_session
from voicegateway.inference._session_context import start_session
from voicegateway.inference._stt import STT
from voicegateway.inference._tts import TTS

__all__ = [
    "LLM",
    "STT",
    "TTS",
    "attach_session",
    "get_active_project",
    "set_project",
    "start_session",
]
