"""Kokoro provider — local TTS via kokoro-onnx."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from voicegateway.providers.base import BaseProvider
from voicegateway.pricing.catalog import get_pricing

logger = logging.getLogger(__name__)


class KokoroProvider(BaseProvider):
    def __init__(self, config: dict[str, Any]):
        self.model_name = config.get("model", "kokoro-v0.19")
        self.default_voice = config.get("voice", "af_heart")
        self._kokoro = None

    def _ensure_library(self):
        try:
            import kokoro_onnx
            return kokoro_onnx
        except ImportError:
            raise ImportError(
                "kokoro-onnx not installed. Run: pip install voicegateway[kokoro]"
            )

    def create_stt(self, model: str, **kwargs: Any) -> Any:
        self._unsupported("stt")

    def create_llm(self, model: str, **kwargs: Any) -> Any:
        self._unsupported("llm")

    def create_tts(self, model: str, voice: str | None = None, **kwargs: Any) -> Any:
        self._ensure_library()
        return KokoroTTS(
            voice=voice or self.default_voice,
        )

    async def health_check(self) -> bool:
        try:
            self._ensure_library()
            return True
        except ImportError:
            return False

    def get_pricing(self, model: str, modality: str) -> dict[str, float]:
        return {"per_character": 0.0}


class KokoroTTS:
    """Local Kokoro TTS using kokoro-onnx.

    Lazily loads the model on first use and keeps it in memory.
    """

    def __init__(self, voice: str = "af_heart"):
        self._voice = voice
        self._model = None

    async def _load_model(self):
        if self._model is None:
            import kokoro_onnx
            logger.info("Loading Kokoro TTS model...")
            self._model = await asyncio.to_thread(kokoro_onnx.Kokoro)
        return self._model

    async def synthesize(self, text: str) -> bytes:
        """Synthesize text to audio bytes."""
        model = await self._load_model()
        samples, sample_rate = await asyncio.to_thread(
            model.create, text, voice=self._voice, speed=1.0
        )
        import numpy as np
        audio_int16 = (samples * 32767).astype(np.int16)
        return audio_int16.tobytes()
