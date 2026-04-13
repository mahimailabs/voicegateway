"""Piper provider — local TTS via piper-tts."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from voicegateway.providers.base import BaseProvider
from voicegateway.pricing.catalog import get_pricing

logger = logging.getLogger(__name__)


class PiperProvider(BaseProvider):
    def __init__(self, config: dict[str, Any]):
        self.model_dir = config.get("model_dir", "~/.local/share/piper-voices/")
        self.default_voice = config.get("default_voice", "en_US-lessac-medium")

    def _ensure_library(self):
        try:
            import piper
            return piper
        except ImportError:
            raise ImportError(
                "piper-tts not installed. Run: pip install voicegateway[piper]"
            )

    def create_stt(self, model: str, **kwargs: Any) -> Any:
        self._unsupported("stt")

    def create_llm(self, model: str, **kwargs: Any) -> Any:
        self._unsupported("llm")

    def create_tts(self, model: str, voice: str | None = None, **kwargs: Any) -> Any:
        self._ensure_library()
        return PiperTTS(
            voice=voice or self.default_voice,
            model_dir=self.model_dir,
        )

    async def health_check(self) -> bool:
        try:
            self._ensure_library()
            return True
        except ImportError:
            return False

    def get_pricing(self, model: str, modality: str) -> dict[str, float]:
        return {"per_character": 0.0}


class PiperTTS:
    """Local Piper TTS.

    Lazily loads the voice model on first use.
    """

    def __init__(self, voice: str = "en_US-lessac-medium",
                 model_dir: str = "~/.local/share/piper-voices/"):
        self._voice = voice
        self._model_dir = model_dir
        self._piper = None

    async def _load_model(self):
        if self._piper is None:
            import piper
            from pathlib import Path
            model_dir = Path(self._model_dir).expanduser()
            model_path = model_dir / f"{self._voice}.onnx"
            logger.info(f"Loading Piper TTS model: {model_path}")
            self._piper = await asyncio.to_thread(
                piper.PiperVoice.load, str(model_path)
            )
        return self._piper

    async def synthesize(self, text: str) -> bytes:
        """Synthesize text to audio bytes."""
        voice = await self._load_model()
        import io
        import wave
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav:
            voice.synthesize(text, wav)
        return wav_buffer.getvalue()
