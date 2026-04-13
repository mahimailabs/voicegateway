"""AssemblyAI provider — STT via livekit-plugins-assemblyai."""

from __future__ import annotations

import os
from typing import Any

from voicegateway.providers.base import BaseProvider
from voicegateway.pricing.catalog import get_pricing


class AssemblyAIProvider(BaseProvider):
    def __init__(self, config: dict[str, Any]):
        self.api_key = config.get("api_key") or os.environ.get("ASSEMBLYAI_API_KEY")

    def _ensure_plugin(self):
        try:
            from livekit.plugins import assemblyai
            return assemblyai
        except ImportError:
            raise ImportError(
                "AssemblyAI plugin not installed. Run: pip install voicegateway[assemblyai]"
            )

    def create_stt(self, model: str, **kwargs: Any) -> Any:
        assemblyai = self._ensure_plugin()
        opts = {**kwargs}
        if self.api_key:
            opts["api_key"] = self.api_key
        return assemblyai.STT(**opts)

    def create_llm(self, model: str, **kwargs: Any) -> Any:
        self._unsupported("llm")

    def create_tts(self, model: str, voice: str | None = None, **kwargs: Any) -> Any:
        self._unsupported("tts")

    async def health_check(self) -> bool:
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.assemblyai.com/v2/transcript",
                    headers={"authorization": self.api_key},
                    timeout=5.0,
                )
                return resp.status_code in (200, 401)
        except Exception:
            return False

    def get_pricing(self, model: str, modality: str) -> dict[str, float]:
        return get_pricing(f"assemblyai/{model}", modality)
