"""Drop-in mirror of `livekit.agents.inference` backed by VoiceGateway."""

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
