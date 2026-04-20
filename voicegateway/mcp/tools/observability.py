"""Observability tools — read-only views of gateway state, costs, latency, logs."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from voicegateway import __version__ as VOICEGW_VERSION
from voicegateway.mcp.errors import ValidationError
from voicegateway.mcp.schemas import (
    GetCostsInput,
    GetHealthInput,
    GetLatencyStatsInput,
    GetLogsInput,
    GetProviderStatusInput,
)
from voicegateway.mcp.tools.base import ToolDef, make_tool

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway


# ---------------------------------------------------------------------------
# Module-level state so get_health can report uptime.
# ---------------------------------------------------------------------------

_STARTED_AT = time.time()


def _parse(model_cls: type, arguments: dict[str, Any]) -> Any:
    try:
        return model_cls(**arguments)
    except Exception as exc:  # pydantic.ValidationError
        raise ValidationError(str(exc)) from exc


# ---------------------------------------------------------------------------
# get_health
# ---------------------------------------------------------------------------

GET_HEALTH_DOC = """Return the overall health and identity of the VoiceGateway instance.

Use this to answer "Is the gateway running ok?" or "What version am I on?"
before running other tools. It never errors — it always returns a status
snapshot — so it is a cheap first call for an agent that just connected.

Args:
    (none)

Returns:
    A dict with fields: version (str), uptime_seconds (float), status ("ok"),
    db_configured (bool), project_count (int), provider_count (int),
    observability (dict with latency_tracking, cost_tracking flags).
"""


async def _handle_get_health(
    gateway: Gateway, arguments: dict[str, Any]
) -> dict[str, Any]:
    _parse(GetHealthInput, arguments)
    cfg = gateway.config
    return {
        "version": VOICEGW_VERSION,
        "uptime_seconds": round(time.time() - _STARTED_AT, 1),
        "status": "ok",
        "db_configured": gateway.storage is not None,
        "project_count": len(cfg.projects),
        "provider_count": len(cfg.providers),
        "observability": {
            "latency_tracking": cfg.observability.get("latency_tracking", True),
            "cost_tracking": cfg.observability.get("cost_tracking", True),
            "request_logging": cfg.observability.get("request_logging", True),
        },
    }


# ---------------------------------------------------------------------------
# get_provider_status
# ---------------------------------------------------------------------------

GET_PROVIDER_STATUS_DOC = """Return the configured status of providers.

Use this to answer "Is Deepgram configured?" or "Which providers are set up?"
It reports whether each provider has credentials configured, its type
(cloud/local), and how many models are registered against it. It does NOT
make live network calls — for a connectivity check, use ``test_provider``.

Args:
    provider_id: Optional — if set, returns only that provider. If omitted,
        returns every provider in the config.

Returns:
    A dict keyed by provider id. Each entry: {configured (bool), type
    ("cloud" | "local"), model_count (int), has_api_key (bool)}.
"""


async def _handle_get_provider_status(
    gateway: Gateway, arguments: dict[str, Any]
) -> dict[str, Any]:
    payload = _parse(GetProviderStatusInput, arguments)
    cfg = gateway.config

    local_names = {"ollama", "whisper", "kokoro", "piper"}

    def _status_for(name: str, provider_cfg: dict[str, Any]) -> dict[str, Any]:
        has_key = bool(provider_cfg.get("api_key"))
        is_local = name in local_names
        model_count = 0
        for modality_models in cfg.models.values():
            if not isinstance(modality_models, dict):
                continue
            for model_cfg in modality_models.values():
                if isinstance(model_cfg, dict) and model_cfg.get("provider") == name:
                    model_count += 1
        return {
            "configured": has_key or is_local,
            "type": "local" if is_local else "cloud",
            "model_count": model_count,
            "has_api_key": has_key,
        }

    if payload.provider_id is not None:
        pcfg = cfg.providers.get(payload.provider_id)
        if pcfg is None:
            return {"providers": {}, "missing": [payload.provider_id]}
        return {
            "providers": {payload.provider_id: _status_for(payload.provider_id, pcfg)}
        }

    return {
        "providers": {
            name: _status_for(name, pcfg) for name, pcfg in cfg.providers.items()
        }
    }


# ---------------------------------------------------------------------------
# get_costs
# ---------------------------------------------------------------------------

GET_COSTS_DOC = """Return cost data for a period, optionally filtered by project.

Use this to answer "How much did we spend today?" or "What's tonys-pizza
spending this month?" The result is derived from the SQLite request log,
so it reflects what was actually invoked, not what was budgeted.

Args:
    period: One of "today", "week", "month", "all". Defaults to "today".
    project: Optional project id. If set, only that project's costs are
        returned. If omitted, costs for all projects are aggregated.

Returns:
    A dict with total_usd, by_provider, by_model, and (when unfiltered)
    by_project breakdowns. If the database isn't enabled, returns zeros.
"""


async def _handle_get_costs(
    gateway: Gateway, arguments: dict[str, Any]
) -> dict[str, Any]:
    payload = _parse(GetCostsInput, arguments)
    if gateway.storage is None:
        return {
            "period": payload.period,
            "project": payload.project,
            "total_usd": 0.0,
            "by_provider": {},
            "by_model": {},
            "by_project": {},
        }
    summary = await gateway.storage.get_cost_summary(
        payload.period, project=payload.project
    )
    result = {
        "period": payload.period,
        "project": payload.project,
        "total_usd": summary.get("total", 0.0),
        "by_provider": summary.get("by_provider", {}),
        "by_model": summary.get("by_model", {}),
    }
    if payload.project is None:
        result["by_project"] = await gateway.storage.get_cost_by_project(payload.period)
    else:
        result["by_project"] = {}
    return result


# ---------------------------------------------------------------------------
# get_latency_stats
# ---------------------------------------------------------------------------

GET_LATENCY_STATS_DOC = """Return latency statistics for the gateway's request log.

Use this to answer "What's our P95 TTFB this week?" or "Which model has
the worst latency?" Stats are computed per-model across the requested
period, and also aggregated into overall percentiles.

Args:
    period: One of "today", "week", "month". Defaults to "today".
    project: Optional project id for filtering.
    modality: Optional "stt" | "llm" | "tts" filter.

Returns:
    {
      overall: {
        ttfb_percentiles: {p50, p95, p99},  # request-level, not averages
        latency_percentiles: {p50, p95, p99},
        avg_ttfb_ms, avg_latency_ms, request_count,
      },
      by_model: {
        <model_id>: {
          avg_ttfb_ms, avg_latency_ms, request_count,
          ttfb_percentiles: {p50, p95, p99},
          latency_percentiles: {p50, p95, p99},
        },
      },
    }
"""


async def _handle_get_latency_stats(
    gateway: Gateway, arguments: dict[str, Any]
) -> dict[str, Any]:
    from voicegateway.storage._percentiles import compute_percentiles

    payload = _parse(GetLatencyStatsInput, arguments)
    pcts = gateway.config.latency.get("percentiles") or [50.0, 95.0, 99.0]

    empty_buckets = compute_percentiles([], pcts)

    if gateway.storage is None:
        return {
            "overall": {
                "ttfb_percentiles": empty_buckets,
                "latency_percentiles": empty_buckets,
                "avg_ttfb_ms": 0.0,
                "avg_latency_ms": 0.0,
                "request_count": 0,
            },
            "by_model": {},
            "period": payload.period,
            "project": payload.project,
            "modality": payload.modality,
        }

    by_model = await gateway.storage.get_latency_stats(
        payload.period, project=payload.project, percentiles=pcts
    )

    # Optional modality filter — storage keys by model_id only.
    if payload.modality:
        modality_map = gateway.config.models.get(payload.modality) or {}
        by_model = {
            model_id: stats
            for model_id, stats in by_model.items()
            if model_id in modality_map
        }

    # Real overall percentiles from the raw samples (not per-model averages).
    ttfb_samples, total_samples = await gateway.storage.get_latency_samples(
        payload.period, project=payload.project
    )
    request_count = len(ttfb_samples)
    avg_ttfb = sum(ttfb_samples) / request_count if request_count else 0.0
    avg_total = (
        sum(total_samples) / len(total_samples) if total_samples else 0.0
    )

    return {
        "period": payload.period,
        "project": payload.project,
        "modality": payload.modality,
        "overall": {
            "ttfb_percentiles": compute_percentiles(ttfb_samples, pcts),
            "latency_percentiles": compute_percentiles(total_samples, pcts),
            "avg_ttfb_ms": avg_ttfb,
            "avg_latency_ms": avg_total,
            "request_count": request_count,
        },
        "by_model": by_model,
    }


# ---------------------------------------------------------------------------
# get_logs (placed here because it is read-only observability)
# ---------------------------------------------------------------------------

GET_LOGS_DOC = """Return recent request logs with optional filters.

Use this to answer "Show me the last 20 errors for tonys-pizza" or
"What was the latency on our last request?" Each row is a record from the
gateway's SQLite log.

Args:
    project: Optional project id filter.
    modality: Optional "stt" | "llm" | "tts" filter.
    model_id: Optional exact model id filter (e.g. "openai/gpt-4o-mini").
    status: Optional "success" | "error" | "fallback" filter.
    limit: Max number of rows to return (1-1000, default 50).

Returns:
    A list of dicts with timestamp, project, modality, model_id, provider,
    cost_usd, ttfb_ms, total_latency_ms, status, error_message.
"""


async def _handle_get_logs(
    gateway: Gateway, arguments: dict[str, Any]
) -> list[dict[str, Any]]:
    payload = _parse(GetLogsInput, arguments)
    if gateway.storage is None:
        return []

    rows = await gateway.storage.get_recent_requests(
        limit=payload.limit,
        modality=payload.modality,
        project=payload.project,
    )

    # Apply additional filters in Python (storage layer only has modality/project).
    if payload.model_id:
        rows = [r for r in rows if r.get("model_id") == payload.model_id]
    if payload.status:
        rows = [r for r in rows if r.get("status") == payload.status]

    return rows


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

OBSERVABILITY_TOOLS: list[ToolDef] = [
    make_tool("get_health", GET_HEALTH_DOC, GetHealthInput, _handle_get_health),
    make_tool(
        "get_provider_status",
        GET_PROVIDER_STATUS_DOC,
        GetProviderStatusInput,
        _handle_get_provider_status,
    ),
    make_tool("get_costs", GET_COSTS_DOC, GetCostsInput, _handle_get_costs),
    make_tool(
        "get_latency_stats",
        GET_LATENCY_STATS_DOC,
        GetLatencyStatsInput,
        _handle_get_latency_stats,
    ),
    make_tool("get_logs", GET_LOGS_DOC, GetLogsInput, _handle_get_logs),
]
