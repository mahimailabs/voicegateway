"""Dashboard endpoints: GET /api/status and GET /api/overview."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Query

from voicegateway.inference.pricing import llm as _llm_pricing
from voicegateway.inference.pricing import stt as _stt_pricing
from voicegateway.inference.pricing import tts as _tts_pricing
from voicegateway.server.api._deps import get_gateway

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

# Local providers don't need an api_key to be considered "configured":
# they run against a local model server (ollama) or bundled binaries
# (whisper / kokoro / piper). Drives the "configured" + "type" fields
# in /api/status so the dashboard can colour the StatusCard correctly.
_LOCAL_PROVIDER_NAMES = frozenset({"ollama", "whisper", "kokoro", "piper"})

router = APIRouter(tags=["dashboard"])


@router.get("/status")
async def get_status(gateway: Gateway = Depends(get_gateway)) -> dict:
    """Get status of all configured providers and models.

    Mirrors the pricing-freshness subtree from /v1/status (Q7) so
    the dashboard StalenessBanner can render without hitting a
    second origin.
    """
    config = gateway.config

    providers: dict[str, dict[str, Any]] = {}
    for name, cfg in config.providers.items():
        is_local = name in _LOCAL_PROVIDER_NAMES
        providers[name] = {
            "configured": bool(cfg.get("api_key")) or is_local,
            "type": "local" if is_local else "cloud",
        }

    models: dict[str, dict[str, Any]] = {}
    for modality, modality_models in config.models.items():
        if isinstance(modality_models, dict):
            for model_id, model_cfg in modality_models.items():
                if isinstance(model_cfg, dict):
                    models[model_id] = {
                        "modality": modality,
                        "provider": model_cfg.get("provider", ""),
                    }

    pricing = {
        "llm": {"source": _llm_pricing.PRICING_SOURCE},
        "stt": {"source": _stt_pricing.PRICING_SOURCE},
        "tts": {"source": _tts_pricing.PRICING_SOURCE},
    }

    return {
        "providers": providers,
        "models": models,
        "fallbacks": config.fallbacks,
        "pricing": pricing,
    }


@router.get("/overview")
async def get_overview(
    project: str | None = Query(None),
    gateway: Gateway = Depends(get_gateway),
) -> dict:
    """Get dashboard overview stats, optionally filtered by project."""
    config = gateway.config

    model_count = 0
    for modality_models in config.models.values():
        if isinstance(modality_models, dict):
            model_count += len(modality_models)

    if gateway.storage is None:
        return {
            "total_requests": 0,
            "total_cost": 0.0,
            "active_models": model_count,
            "providers_configured": len(config.providers),
        }

    cost_summary = await gateway.storage.get_cost_summary("today", project=project)
    total_all = await gateway.storage.get_cost_summary("all", project=project)

    return {
        "total_requests": sum(
            d["requests"] for d in total_all.get("by_provider", {}).values()
        ),
        "total_cost_today": cost_summary.get("total", 0.0),
        "total_cost_all": total_all.get("total", 0.0),
        "active_models": model_count,
        "providers_configured": len(config.providers),
    }
