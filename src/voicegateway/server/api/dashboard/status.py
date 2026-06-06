"""Dashboard endpoints: GET /api/status and GET /api/overview."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Query

from voicegateway._version import __version__
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

# Public version for the dashboard footer (local git segment stripped).
_PUBLIC_VERSION = __version__.split("+", 1)[0]


def _configured_provider_names(config) -> set[str]:
    """Providers the operator can actually call: a cloud key is set, or local."""
    return {
        name
        for name, cfg in config.providers.items()
        if bool(cfg.get("api_key")) or name in _LOCAL_PROVIDER_NAMES
    }


router = APIRouter(tags=["dashboard"])


@router.get("/status")
async def get_status(gateway: Gateway = Depends(get_gateway)) -> dict:
    """Status of configured providers and the models the operator can call.

    Models are filtered to providers that are usable (a cloud key is set, or
    the provider is local), so the count and the Models page stay honest. Also
    mirrors the pricing-freshness subtree from /v1/status so the dashboard can
    render it without hitting a second origin.
    """
    config = gateway.config

    providers: dict[str, dict[str, Any]] = {}
    for name, cfg in config.providers.items():
        is_local = name in _LOCAL_PROVIDER_NAMES
        providers[name] = {
            "configured": bool(cfg.get("api_key")) or is_local,
            "type": "local" if is_local else "cloud",
        }

    # Only surface models whose provider is actually usable (see
    # _configured_provider_names); a roster full of models the operator can't
    # call is noise.
    configured_providers = _configured_provider_names(config)
    models: dict[str, dict[str, Any]] = {}
    for modality, modality_models in config.models.items():
        if isinstance(modality_models, dict):
            for model_id, model_cfg in modality_models.items():
                if isinstance(model_cfg, dict):
                    provider = model_cfg.get("provider", "")
                    if provider in configured_providers:
                        models[model_id] = {
                            "modality": modality,
                            "provider": provider,
                        }

    pricing = {
        "llm": {"source": _llm_pricing.PRICING_SOURCE},
        "stt": {"source": _stt_pricing.PRICING_SOURCE},
        "tts": {"source": _tts_pricing.PRICING_SOURCE},
    }

    return {
        "version": _PUBLIC_VERSION,
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

    # Count only callable models, so "Active Models" matches the filtered
    # /api/status list the sidebar renders.
    configured_providers = _configured_provider_names(config)
    model_count = 0
    for modality_models in config.models.values():
        if isinstance(modality_models, dict):
            model_count += sum(
                1
                for m in modality_models.values()
                if isinstance(m, dict) and m.get("provider", "") in configured_providers
            )

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
