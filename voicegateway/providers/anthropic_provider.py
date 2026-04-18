"""Anthropic provider — LLM via livekit-plugins-anthropic."""

from __future__ import annotations

import os
from typing import Any

from voicegateway.pricing.catalog import get_pricing
from voicegateway.providers.base import BaseProvider


class AnthropicProvider(BaseProvider):
    def __init__(self, config: dict[str, Any]):
        self.api_key = config.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")

    def _ensure_plugin(self):
        try:
            from livekit.plugins import anthropic

            return anthropic
        except ImportError as e:
            raise ImportError(
                "Anthropic plugin not installed. Run: pip install voicegateway[anthropic]"
            ) from e

    def create_stt(self, model: str, **kwargs: Any) -> Any:
        self._unsupported("stt")

    def create_llm(self, model: str, **kwargs: Any) -> Any:
        anthropic = self._ensure_plugin()
        opts = {"model": model, **kwargs}
        if self.api_key:
            opts["api_key"] = self.api_key
        return anthropic.LLM(**opts)

    def create_tts(self, model: str, voice: str | None = None, **kwargs: Any) -> Any:
        self._unsupported("tts")

    async def health_check(self) -> bool:
        import httpx

        if not self.api_key:
            return False
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={
                        "x-api-key": self.api_key,
                        "anthropic-version": "2023-06-01",
                    },
                    timeout=5.0,
                )
                return resp.status_code == 200
        except Exception:
            return False

    def get_pricing(self, model: str, modality: str) -> dict[str, float]:
        return get_pricing(f"anthropic/{model}", modality)
