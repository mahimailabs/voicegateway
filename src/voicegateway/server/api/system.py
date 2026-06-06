"""System endpoints: /health and /v1/status."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request

from voicegateway._version import __version__
from voicegateway.inference.pricing import llm as _llm_pricing
from voicegateway.inference.pricing import stt as _stt_pricing
from voicegateway.inference.pricing import tts as _tts_pricing
from voicegateway.server.api._deps import get_gateway

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(tags=["system"])

# Drop the PEP 440 local segment (the +g<sha>.d<date> git metadata an
# editable install carries) so health/status report a clean public version.
_PUBLIC_VERSION = __version__.split("+", 1)[0]


@router.get("/health")
async def health(request: Request) -> dict:
    started_at = getattr(request.app.state, "started_at", time.time())
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - started_at, 1),
        "version": _PUBLIC_VERSION,
    }


@router.get("/v1/status")
async def v1_status(gateway: Gateway = Depends(get_gateway)) -> dict:
    cfg = gateway.config
    providers = {}
    for name, provider_cfg in cfg.providers.items():
        has_key = bool(provider_cfg.get("api_key")) or name in (
            "ollama",
            "whisper",
            "kokoro",
            "piper",
        )
        providers[name] = {
            "configured": has_key,
            "type": "local"
            if name in ("ollama", "whisper", "kokoro", "piper")
            else "cloud",
        }
    pricing = {
        "llm": {"source": _llm_pricing.PRICING_SOURCE},
        "stt": {"source": _stt_pricing.PRICING_SOURCE},
        "tts": {"source": _tts_pricing.PRICING_SOURCE},
    }
    return {
        "providers": providers,
        "model_count": sum(len(v) for v in cfg.models.values() if isinstance(v, dict)),
        "project_count": len(cfg.projects),
        "pricing": pricing,
    }
