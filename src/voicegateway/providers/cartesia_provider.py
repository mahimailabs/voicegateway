"""Cartesia provider — TTS via livekit-plugins-cartesia."""

from __future__ import annotations

import os
from typing import Any

from voicegateway.providers.base import BaseProvider

# Cartesia API requires a Cartesia-Version header on every request and
# 400s without it. livekit-plugins-cartesia sets this internally on
# the TTS path, so live agents work, but the bypass health_check below
# uses httpx directly and must send the header itself. Pin to the same
# value the installed LK plugin uses (livekit.plugins.cartesia.constants.API_VERSION)
# so a Cartesia version bump only requires updating one constant alongside
# the LK pin.
_CARTESIA_API_VERSION = "2025-04-16"


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
                    headers={
                        "X-API-Key": self.api_key,
                        "Cartesia-Version": _CARTESIA_API_VERSION,
                    },
                    timeout=5.0,
                )
                return resp.status_code == 200
        except Exception:
            return False
