"""Whisper provider — local STT via faster-whisper."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from voicegateway.providers.base import BaseProvider

logger = logging.getLogger(__name__)


class WhisperProvider(BaseProvider):
    def __init__(self, config: dict[str, Any]):
        self.model_path = config.get("model_path", "large-v3")
        self.device = config.get("device", "auto")
        self.compute_type = config.get("compute_type", "float16")
        self._model = None

    def _ensure_library(self):
        try:
            import faster_whisper
            return faster_whisper
        except ImportError as e:
            raise ImportError(
                "faster-whisper not installed. Run: pip install voicegateway[whisper]"
            ) from e

    def create_stt(self, model: str, **kwargs: Any) -> Any:
        """Create a Whisper STT that uses StreamAdapter for streaming."""
        self._ensure_library()
        return WhisperSTT(
            model=model or self.model_path,
            device=self.device,
            compute_type=self.compute_type,
        )

    def create_llm(self, model: str, **kwargs: Any) -> Any:
        self._unsupported("llm")

    def create_tts(self, model: str, voice: str | None = None, **kwargs: Any) -> Any:
        self._unsupported("tts")

    async def health_check(self) -> bool:
        try:
            self._ensure_library()
            return True
        except ImportError:
            return False

    def get_pricing(self, model: str, modality: str) -> dict[str, float]:
        return {"per_minute": 0.0}


class WhisperSTT:
    """Local Whisper STT using faster-whisper with StreamAdapter pattern.

    Inherits from livekit.agents.stt.STT to be compatible with LiveKit agents.
    """

    def __init__(self, model: str = "large-v3", device: str = "auto",
                 compute_type: str = "float16"):

        self._model_name = model
        self._device = device
        self._compute_type = compute_type
        self._whisper_model = None
        # Create base STT-compatible interface
        self._stt_impl = _WhisperSTTImpl(self)

    def _load_model(self):
        if self._whisper_model is None:
            from faster_whisper import WhisperModel
            device = self._device
            if device == "auto":
                import platform
                if platform.processor() == "arm":
                    device = "cpu"
                else:
                    try:
                        import torch
                        device = "cuda" if torch.cuda.is_available() else "cpu"
                    except ImportError:
                        device = "cpu"
            logger.info(f"Loading Whisper model '{self._model_name}' on {device}")
            self._whisper_model = WhisperModel(
                self._model_name,
                device=device,
                compute_type=self._compute_type if device != "cpu" else "int8",
            )
        return self._whisper_model

    async def recognize(self, buffer) -> Any:
        """Transcribe audio buffer."""
        model = await asyncio.to_thread(self._load_model)
        import numpy as np
        if hasattr(buffer, 'data'):
            audio_data = np.frombuffer(buffer.data, dtype=np.int16).astype(np.float32) / 32768.0
        else:
            audio_data = buffer

        segments, info = await asyncio.to_thread(
            model.transcribe, audio_data, beam_size=5
        )
        segments = list(segments)
        text = " ".join(s.text.strip() for s in segments)
        return text

    def __getattr__(self, name):
        return getattr(self._stt_impl, name)


class _WhisperSTTImpl:
    """Internal STT implementation wrapper."""

    def __init__(self, whisper_stt: WhisperSTT):
        self._whisper = whisper_stt

    async def recognize(self, buffer) -> Any:
        return await self._whisper.recognize(buffer)
