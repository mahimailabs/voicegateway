"""Cartesia provider — TTS via livekit-plugins-cartesia."""

from __future__ import annotations

import os
from typing import Any

from voicegateway.pricing.catalog import get_pricing
from voicegateway.providers.base import BaseProvider


class CartesiaProvider(BaseProvider):
    def __init__(self, config: dict[str, Any]):
        self.api_key = config.get("api_key") or os.environ.get("CARTESIA_API_KEY")

    def _ensure_plugin(self):
        try:
            from livekit.plugins import cartesia
            return cartesia
        except ImportError as e:
            raise ImportError(
                "Cartesia plugin not installed. Run: pip install voicegateway[cartesia]"
            ) from e

    def create_stt(self, model: str, **kwargs: Any) -> Any:
        self._unsupported("stt")

    def create_llm(self, model: str, **kwargs: Any) -> Any:
        self._unsupported("llm")

    def create_tts(self, model: str, voice: str | None = None, **kwargs: Any) -> Any:
        cartesia = self._ensure_plugin()
        opts = {"model": model, **kwargs}
        if self.api_key:
            opts["api_key"] = self.api_key
        if voice:
            opts["voice"] = voice
        return cartesia.TTS(**opts)

    async def health_check(self) -> bool:
        import httpx
        if not self.api_key:
            return False
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.cartesia.ai/voices",
                    headers={"X-API-Key": self.api_key},
                    timeout=5.0,
                )
                return resp.status_code == 200
        except Exception:
            return False

    def get_pricing(self, model: str, modality: str) -> dict[str, float]:
        return get_pricing(f"cartesia/{model}", modality)
