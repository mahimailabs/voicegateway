"""Prometheus-format metrics endpoint: GET /v1/metrics."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse

from voicegateway.server.api._deps import get_gateway
from voicegateway.utils.percentiles import quantile_label

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("", response_class=PlainTextResponse)
async def prometheus_metrics(
    request: Request,
    gateway: Gateway = Depends(get_gateway),
) -> str:
    """Prometheus-format metrics."""
    started_at = getattr(request.app.state, "started_at", time.time())
    lines = [
        "# HELP voicegw_uptime_seconds Process uptime",
        "# TYPE voicegw_uptime_seconds gauge",
        f"voicegw_uptime_seconds {time.time() - started_at:.1f}",
        "# HELP voicegw_providers_configured Configured providers",
        "# TYPE voicegw_providers_configured gauge",
        f"voicegw_providers_configured {len(gateway.config.providers)}",
        "# HELP voicegw_projects_configured Configured projects",
        "# TYPE voicegw_projects_configured gauge",
        f"voicegw_projects_configured {len(gateway.config.projects)}",
    ]

    if gateway.storage is not None:
        today = await gateway.storage.get_cost_summary("today")
        lines += [
            "# HELP voicegw_cost_usd_total Total cost in USD (today)",
            "# TYPE voicegw_cost_usd_total counter",
            f'voicegw_cost_usd_total{{period="today"}} {today["total"]:.6f}',
            "# HELP voicegw_requests_total Total requests (today)",
            "# TYPE voicegw_requests_total counter",
        ]
        for provider, data in today.get("by_provider", {}).items():
            lines.append(
                f'voicegw_requests_total{{provider="{provider}"}} {data["requests"]}'
            )
            lines.append(
                f'voicegw_cost_usd_total{{provider="{provider}"}} {data["cost"]:.6f}'
            )

        by_project = await gateway.storage.get_cost_by_project("today")
        for pid, data in by_project.items():
            lines.append(
                f'voicegw_cost_usd_total{{project="{pid}"}} {data["cost"]:.6f}'
            )

        pcts = gateway.config.latency.get("percentiles") or [50.0, 95.0, 99.0]
        latency = await gateway.storage.get_latency_stats("today", percentiles=pcts)
        if latency:
            lines += [
                "# HELP voicegw_request_ttfb_seconds "
                "Per-model time to first byte (seconds, summary)",
                "# TYPE voicegw_request_ttfb_seconds summary",
                "# HELP voicegw_request_total_latency_seconds "
                "Per-model total latency (seconds, summary)",
                "# TYPE voicegw_request_total_latency_seconds summary",
            ]
            for model, s in latency.items():
                for p in pcts:
                    key = f"p{int(p)}"
                    q = quantile_label(p)
                    ttfb_v = s.get("ttfb_percentiles", {}).get(key)
                    if ttfb_v is not None:
                        lines.append(
                            f"voicegw_request_ttfb_seconds"
                            f'{{model="{model}",quantile="{q}"}} '
                            f"{ttfb_v / 1000:.6f}"
                        )
                    lat_v = s.get("latency_percentiles", {}).get(key)
                    if lat_v is not None:
                        lines.append(
                            f"voicegw_request_total_latency_seconds"
                            f'{{model="{model}",quantile="{q}"}} '
                            f"{lat_v / 1000:.6f}"
                        )

    return "\n".join(lines) + "\n"
