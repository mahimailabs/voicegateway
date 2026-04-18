"""Groq provider — LLM and STT via OpenAI-compatible interface."""

from __future__ import annotations

import os
from typing import Any

from voicegateway.pricing.catalog import get_pricing
from voicegateway.providers.base import BaseProvider


class GroqProvider(BaseProvider):
    def __init__(self, config: dict[str, Any]):
        self.api_key = config.get("api_key") or os.environ.get("GROQ_API_KEY")
        self.base_url = config.get("base_url", "https://api.groq.com/openai/v1")

    def _ensure_plugin(self):
        try:
            from livekit.plugins import openai

            return openai
        except ImportError as e:
            raise ImportError(
                "OpenAI plugin not installed (required for Groq). "
                "Run: pip install voicegateway[groq]"
            ) from e

    def create_stt(self, model: str, **kwargs: Any) -> Any:
        openai = self._ensure_plugin()
        return openai.STT(
            model=model,
            base_url=self.base_url,
            api_key=self.api_key,
            **kwargs,
        )

    def create_llm(self, model: str, **kwargs: Any) -> Any:
        openai = self._ensure_plugin()
        return openai.LLM(
            model=model,
            base_url=self.base_url,
            api_key=self.api_key,
            **kwargs,
        )

    def create_tts(self, model: str, voice: str | None = None, **kwargs: Any) -> Any:
        self._unsupported("tts")

    async def health_check(self) -> bool:
        import httpx

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.base_url}/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=5.0,
                )
                return resp.status_code == 200
        except Exception:
            return False

    def get_pricing(self, model: str, modality: str) -> dict[str, float]:
        return get_pricing(f"groq/{model}", modality)
