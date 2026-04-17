"""Ollama provider — LLM via OpenAI-compatible interface."""

from __future__ import annotations

from typing import Any

from voicegateway.providers.base import BaseProvider


class OllamaProvider(BaseProvider):
    # Default timeout is generous because local models can take 10–30s
    # to load into memory on the first request after a cold start.
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
                "Run: pip install voicegateway[openai]"
            ) from e

    def create_stt(self, model: str, **kwargs: Any) -> Any:
        self._unsupported("stt")

    def create_llm(self, model: str, **kwargs: Any) -> Any:
        openai = self._ensure_plugin()
        # Build a long-timeout httpx client so cold-start model loading
        # doesn't trip the OpenAI plugin's default 5s timeout.
        import httpx
        from openai import AsyncOpenAI

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
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.base_url}/api/tags", timeout=5.0)
                return resp.status_code == 200
        except Exception:
            return False

    def get_pricing(self, model: str, modality: str) -> dict[str, float]:
        return {"input_per_1k": 0.0, "output_per_1k": 0.0}
