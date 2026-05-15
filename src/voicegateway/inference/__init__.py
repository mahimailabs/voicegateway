"""Drop-in mirror of `livekit.agents.inference` backed by VoiceGateway."""

from voicegateway.inference.llm_inference import LLM
from voicegateway.core.active_project import get_active_project, set_project
from voicegateway.inference.session.attach import attach_session
from voicegateway.inference.session.context import start_session
from voicegateway.inference.stt_inference import STT
from voicegateway.inference.tts_inference import TTS

__all__ = [
    "LLM",
    "STT",
    "TTS",
    "attach_session",
    "get_active_project",
    "set_project",
    "start_session",
]
