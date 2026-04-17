"""ElevenLabs provider — TTS via livekit-plugins-elevenlabs."""

from __future__ import annotations

import os
from typing import Any

from voicegateway.pricing.catalog import get_pricing
from voicegateway.providers.base import BaseProvider


class ElevenLabsProvider(BaseProvider):
    def __init__(self, config: dict[str, Any]):
        self.api_key = config.get("api_key") or os.environ.get("ELEVENLABS_API_KEY")

    def _ensure_plugin(self):
        try:
            from livekit.plugins import elevenlabs
            return elevenlabs
        except ImportError as e:
            raise ImportError(
                "ElevenLabs plugin not installed. Run: pip install voicegateway[elevenlabs]"
            ) from e

    def create_stt(self, model: str, **kwargs: Any) -> Any:
        self._unsupported("stt")

    def create_llm(self, model: str, **kwargs: Any) -> Any:
        self._unsupported("llm")

    def create_tts(self, model: str, voice: str | None = None, **kwargs: Any) -> Any:
        elevenlabs = self._ensure_plugin()
        opts = {"model": model, **kwargs}
        if self.api_key:
            opts["api_key"] = self.api_key
        if voice:
            opts["voice_id"] = voice
        return elevenlabs.TTS(**opts)

    async def health_check(self) -> bool:
        import httpx
        if not self.api_key:
            return False
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.elevenlabs.io/v1/voices",
                    headers={"xi-api-key": self.api_key},
                    timeout=5.0,
                )
                return resp.status_code == 200
        except Exception:
            return False

    def get_pricing(self, model: str, modality: str) -> dict[str, float]:
        return get_pricing(f"elevenlabs/{model}", modality)
