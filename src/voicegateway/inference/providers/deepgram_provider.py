"""Deepgram provider — STT and TTS via livekit-plugins-deepgram."""

from __future__ import annotations

import os
from typing import Any

from voicegateway.inference.providers.base_provider import BaseProvider


class DeepgramProvider(BaseProvider):
    def __init__(self, config: dict[str, Any]):
        self.api_key = config.get("api_key") or os.environ.get("DEEPGRAM_API_KEY")

    def _ensure_plugin(self):
        try:
            from livekit.plugins import deepgram

            return deepgram
        except ImportError as e:
            raise ImportError(
                "Deepgram plugin not installed. Run: pip install voicegateway[deepgram]"
            ) from e

    def create_stt(self, model: str, **kwargs: Any) -> Any:
        deepgram = self._ensure_plugin()
        opts = {"model": model, **kwargs}
        if self.api_key:
            opts["api_key"] = self.api_key
        return deepgram.STT(**opts)

    def create_llm(self, model: str, **kwargs: Any) -> Any:
        self._unsupported("llm")

    def create_tts(self, model: str, voice: str | None = None, **kwargs: Any) -> Any:
        deepgram = self._ensure_plugin()
        if voice and voice not in model:
            model = f"{model}-{voice}-en"
        opts = {"model": model, **kwargs}
        if self.api_key:
            opts["api_key"] = self.api_key
        return deepgram.TTS(**opts)

    async def health_check(self) -> bool:
        import httpx

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.deepgram.com/v1/projects",
                    headers={"Authorization": f"Token {self.api_key}"},
                    timeout=5.0,
                )
                return resp.status_code == 200
        except Exception:
            return False
