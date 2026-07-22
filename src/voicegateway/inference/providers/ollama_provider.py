"""Ollama provider — LLM via OpenAI-compatible interface."""

from __future__ import annotations

from typing import Any

import httpx
from openai import AsyncOpenAI

from voicegateway.inference.providers.base_provider import BaseProvider


class OllamaProvider(BaseProvider):
    DEFAULT_TIMEOUT_SECONDS = 120.0

    def __init__(self, config: dict[str, Any]):
        self.base_url = config.get("base_url", "http://localhost:11434")
        self.timeout = config.get("timeout", self.DEFAULT_TIMEOUT_SECONDS)

    def _ensure_plugin(self):
        try:
            from livekit.plugins import openai

            return openai
        except ImportError as e:
            raise ImportError(
                "OpenAI plugin not installed (required for Ollama). "
                "Run: pip install livekit-plugins-openai"
            ) from e

    def create_stt(self, model: str, **kwargs: Any) -> Any:
        self._unsupported("stt")

    def create_llm(self, model: str, **kwargs: Any) -> Any:
        openai = self._ensure_plugin()

        client = AsyncOpenAI(
            base_url=f"{self.base_url}/v1",
            api_key="ollama",
            http_client=httpx.AsyncClient(timeout=self.timeout),
        )
        return openai.LLM(
            model=model,
            client=client,
            **kwargs,
        )

    def create_tts(self, model: str, voice: str | None = None, **kwargs: Any) -> Any:
        self._unsupported("tts")

    async def health_check(self) -> bool:

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.base_url}/api/tags", timeout=5.0)
                return resp.status_code == 200
        except Exception:
            return False
