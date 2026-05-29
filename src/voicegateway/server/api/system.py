"""System endpoints: /health and /v1/status."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request

from voicegateway.inference.pricing import llm as _llm_pricing
from voicegateway.inference.pricing import stt as _stt_pricing
from voicegateway.inference.pricing import tts as _tts_pricing
from voicegateway.server.api._deps import get_gateway

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(request: Request) -> dict:
    started_at = getattr(request.app.state, "started_at", time.time())
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - started_at, 1),
        "version": "0.5.0",
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
