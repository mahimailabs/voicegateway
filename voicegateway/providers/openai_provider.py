"""OpenAI provider — LLM, TTS, and STT via livekit-plugins-openai."""

from __future__ import annotations

import os
from typing import Any

from voicegateway.pricing.catalog import get_pricing
from voicegateway.providers.base import BaseProvider


class OpenAIProvider(BaseProvider):
    def __init__(self, config: dict[str, Any]):
        self.api_key = config.get("api_key") or os.environ.get("OPENAI_API_KEY")
        self.base_url = config.get("base_url")
        self.api_version = config.get("api_version")

    def _ensure_plugin(self):
        try:
            from livekit.plugins import openai
            return openai
        except ImportError as e:
            raise ImportError(
                "OpenAI plugin not installed. Run: pip install voicegateway[openai]"
            ) from e

    def create_stt(self, model: str, **kwargs: Any) -> Any:
        openai = self._ensure_plugin()
        opts = {"model": model, **kwargs}
        if self.api_key:
            opts["api_key"] = self.api_key
        if self.base_url:
            opts["base_url"] = self.base_url
        return openai.STT(**opts)

    def create_llm(self, model: str, **kwargs: Any) -> Any:
        openai = self._ensure_plugin()
        opts = {"model": model, **kwargs}
        if self.api_key:
            opts["api_key"] = self.api_key
        if self.base_url:
            opts["base_url"] = self.base_url
        return openai.LLM(**opts)

    def create_tts(self, model: str, voice: str | None = None, **kwargs: Any) -> Any:
        openai = self._ensure_plugin()
        opts = {"model": model, **kwargs}
        if self.api_key:
            opts["api_key"] = self.api_key
        if self.base_url:
            opts["base_url"] = self.base_url
        if voice:
            opts["voice"] = voice
        return openai.TTS(**opts)

    async def health_check(self) -> bool:
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                url = self.base_url or "https://api.openai.com"
                resp = await client.get(f"{url}/v1/models", headers={
                    "Authorization": f"Bearer {self.api_key}"
                }, timeout=5.0)
                return resp.status_code == 200
        except Exception:
            return False

    def get_pricing(self, model: str, modality: str) -> dict[str, float]:
        return get_pricing(f"openai/{model}", modality)
