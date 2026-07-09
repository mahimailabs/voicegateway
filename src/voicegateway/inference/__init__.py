"""Drop-in mirror of `livekit.agents.inference` backed by VoiceGateway.

Framework-neutral: importing this package does NOT import ``livekit``. The
``LLM``/``STT``/``TTS`` factories are LiveKit-plugin factories that pull
``livekit`` on first use, so they are resolved lazily via a module-level
``__getattr__`` (PEP 562). The remaining names (``attach``, ``attach_session``,
``start_session``, ``get_active_project``, ``set_project``) are framework-neutral
and imported eagerly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from voicegateway.core.active_project import get_active_project, set_project
from voicegateway.inference.session.attach import attach, attach_session
from voicegateway.inference.session.context import start_session

if TYPE_CHECKING:
    from voicegateway.inference.llm_inference import LLM
    from voicegateway.inference.stt_inference import STT
    from voicegateway.inference.tts_inference import TTS

__all__ = [
    "LLM",
    "STT",
    "TTS",
    "attach",
    "attach_session",
    "get_active_project",
    "set_project",
    "start_session",
]

# Names that pull livekit on first access. Kept out of module-load imports so
# `import voicegateway.inference` stays framework-neutral.
_LAZY = {
    "LLM": ("voicegateway.inference.llm_inference", "LLM"),
    "STT": ("voicegateway.inference.stt_inference", "STT"),
    "TTS": ("voicegateway.inference.tts_inference", "TTS"),
}


def __getattr__(name: str) -> Any:
    """Lazily resolve the LiveKit-plugin factories (PEP 562)."""
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module_name, attr = target
    value = getattr(importlib.import_module(module_name), attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(globals()))
