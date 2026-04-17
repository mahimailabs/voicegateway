"""VoiceGateway HTTP API server.

A thin FastAPI layer over the Gateway, exposing /health, /v1/status,
/v1/models, /v1/costs, /v1/projects, /v1/logs, and /v1/metrics.

This is what the dashboard consumes and what external monitoring tools
(Prometheus, load balancers) scrape.
"""

from __future__ import annotations

import time
from typing import Any, TYPE_CHECKING

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway


def build_app(gateway: "Gateway") -> FastAPI:
    """Build a FastAPI app bound to the given Gateway instance."""
    app = FastAPI(
        title="VoiceGateway API",
        version="0.1.0",
        description="HTTP API for the VoiceGateway self-hosted inference gateway.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    started_at = time.time()

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "uptime_seconds": round(time.time() - started_at, 1),
            "version": "0.1.0",
        }

    @app.get("/v1/status")
    async def v1_status() -> dict:
        cfg = gateway.config
        providers = {}
        for name, provider_cfg in cfg.providers.items():
            has_key = bool(provider_cfg.get("api_key")) or name in (
                "ollama", "whisper", "kokoro", "piper"
            )
            providers[name] = {
                "configured": has_key,
                "type": "local" if name in ("ollama", "whisper", "kokoro", "piper") else "cloud",
            }
        return {
            "providers": providers,
            "model_count": sum(
                len(v) for v in cfg.models.values() if isinstance(v, dict)
            ),
            "project_count": len(cfg.projects),
        }

    @app.get("/v1/models")
    async def v1_models(
        project: str | None = Query(None),
    ) -> dict:
        cfg = gateway.config
        models: dict[str, dict[str, Any]] = {}
        for modality, modality_models in cfg.models.items():
            if not isinstance(modality_models, dict):
                continue
            for model_id, model_cfg in modality_models.items():
                if not isinstance(model_cfg, dict):
                    continue
                models[model_id] = {
                    "modality": modality,
                    "provider": model_cfg.get("provider", ""),
                    "model": model_cfg.get("model", ""),
                }

        # Optionally filter to models used by a project's default stack
        if project:
            pcfg = cfg.get_project(project)
            if pcfg and pcfg.default_stack and pcfg.default_stack in cfg.stacks:
                wanted = set(cfg.stacks[pcfg.default_stack].values())
                models = {k: v for k, v in models.items() if k in wanted}

        return {"models": models, "project": project}

    @app.get("/v1/costs")
    async def v1_costs(
        period: str = Query("today"),
        project: str | None = Query(None),
    ) -> dict:
        if gateway.storage is None:
            return {
                "period": period,
                "project": project,
                "total": 0.0,
                "by_provider": {},
                "by_model": {},
                "by_project": {},
            }
        summary = await gateway.storage.get_cost_summary(period, project=project)
        # Always include a by_project breakdown for the "All Projects" view
        if project is None:
            summary["by_project"] = await gateway.storage.get_cost_by_project(period)
        else:
            summary["by_project"] = {}
        return summary

    @app.get("/v1/latency")
    async def v1_latency(
        period: str = Query("today"),
        project: str | None = Query(None),
    ) -> dict:
        if gateway.storage is None:
            return {}
        return await gateway.storage.get_latency_stats(period, project=project)

    @app.get("/v1/projects")
    async def v1_projects() -> dict:
        """List configured projects with today's stats."""
        projects = gateway.list_projects()
        stats: dict[str, Any] = {}
        if gateway.storage is not None:
            for p in projects:
                pid = p["id"]
                stats[pid] = await gateway.storage.get_project_stats(pid)
        return {"projects": projects, "stats": stats}

    @app.get("/v1/projects/{project_id}")
    async def v1_project_detail(project_id: str) -> dict:
        pcfg = gateway.config.get_project(project_id)
        if pcfg is None:
            return {"error": f"project not found: {project_id}"}
        data: dict[str, Any] = {
            "id": pcfg.id,
            "name": pcfg.name,
            "description": pcfg.description,
            "daily_budget": pcfg.daily_budget,
            "budget_action": pcfg.budget_action,
            "default_stack": pcfg.default_stack,
            "tags": list(pcfg.tags),
            "accent": pcfg.accent,
            "today_spend": 0.0,
            "budget_status": "ok",
        }
        if gateway.storage is not None:
            data["today"] = await gateway.storage.get_project_stats(project_id)
            costs_today = await gateway.storage.get_cost_summary("today", project=project_id)
            data["costs_today"] = costs_today
            today_spend = costs_today.get("total", 0.0)
            data["today_spend"] = today_spend
            enforcer = gateway._budget_enforcer
            data["budget_status"] = enforcer.get_budget_status(project_id, today_spend)
        return data

    @app.get("/v1/logs")
    async def v1_logs(
        limit: int = Query(100, ge=1, le=1000),
        modality: str | None = Query(None),
        project: str | None = Query(None),
    ) -> list[dict]:
        if gateway.storage is None:
            return []
        return await gateway.storage.get_recent_requests(
            limit=limit, modality=modality, project=project
        )

    @app.get("/v1/metrics", response_class=PlainTextResponse)
    async def v1_metrics() -> str:
        """Prometheus-format metrics."""
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

        return "\n".join(lines) + "\n"

    return app
