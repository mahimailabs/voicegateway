"""VoiceGateway HTTP API server.

A thin FastAPI layer over the Gateway, exposing /health, /v1/status,
/v1/models, /v1/costs, /v1/projects, /v1/logs, and /v1/metrics.

This is what the dashboard consumes and what external monitoring tools
(Prometheus, load balancers) scrape.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway


def build_app(gateway: Gateway) -> FastAPI:
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

    # ------------------------------------------------------------------
    # CRUD — Providers
    # ------------------------------------------------------------------

    @app.get("/v1/providers")
    async def v1_list_providers() -> dict:
        from voicegateway.core.crypto import mask

        result = []
        for name, pcfg in gateway.config.providers.items():
            api_key = pcfg.get("api_key", "") if isinstance(pcfg, dict) else ""
            source = pcfg.get("_source", "yaml") if isinstance(pcfg, dict) else "yaml"
            result.append({
                "provider_id": name,
                "source": source,
                "api_key_masked": mask(api_key) if source != "db" else mask(api_key),
                "base_url": pcfg.get("base_url") if isinstance(pcfg, dict) else None,
            })
        return {"providers": result}

    @app.post("/v1/providers")
    async def v1_create_provider(body: dict[str, Any]) -> dict:
        from voicegateway.core.registry import _PROVIDER_REGISTRY

        pid = body.get("provider_id", "")
        ptype = body.get("provider_type", pid)
        api_key = body.get("api_key", "")
        base_url = body.get("base_url")

        if ptype not in _PROVIDER_REGISTRY:
            raise HTTPException(400, f"Unknown provider_type '{ptype}'")
        if pid in gateway.config.providers:
            is_managed = isinstance(gateway.config.providers[pid], dict) and gateway.config.providers[pid].get("_source") == "db"
            if not is_managed:
                raise HTTPException(409, f"Provider '{pid}' already exists in YAML")
        if gateway.storage is None:
            raise HTTPException(400, "Storage not enabled")

        await gateway.storage.upsert_managed_provider(pid, ptype, api_key, base_url)
        await gateway.storage.log_audit_event("provider", pid, "create", body, "api")
        await gateway.refresh_config()

        from voicegateway.core.crypto import mask
        return {"provider_id": pid, "source": "db", "api_key_masked": mask(api_key)}

    @app.patch("/v1/providers/{provider_id}")
    async def v1_update_provider(provider_id: str, body: dict[str, Any]) -> dict:
        if gateway.storage is None:
            raise HTTPException(400, "Storage not enabled")
        existing = await gateway.storage.get_managed_provider(provider_id)
        if existing is None:
            raise HTTPException(404, f"No managed provider '{provider_id}'")

        from voicegateway.core.crypto import decrypt
        current_key = decrypt(existing.get("api_key_encrypted", ""))
        api_key = body.get("api_key", current_key)
        base_url = body.get("base_url", existing.get("base_url"))
        ptype = body.get("provider_type", existing["provider_type"])

        await gateway.storage.upsert_managed_provider(provider_id, ptype, api_key, base_url)
        await gateway.storage.log_audit_event("provider", provider_id, "update", body, "api")
        await gateway.refresh_config()
        return {"provider_id": provider_id, "updated": True}

    @app.delete("/v1/providers/{provider_id}")
    async def v1_delete_provider(
        provider_id: str,
        confirm: bool = Query(False),
    ) -> dict:
        if gateway.storage is None:
            raise HTTPException(400, "Storage not enabled")
        managed = await gateway.storage.get_managed_provider(provider_id)
        if managed is None:
            if provider_id in gateway.config.providers:
                raise HTTPException(403, f"Provider '{provider_id}' is YAML-defined and cannot be deleted")
            raise HTTPException(404, f"No provider '{provider_id}'")

        if not confirm:
            return {"would_delete": {"provider_id": provider_id}}
        await gateway.storage.delete_managed_provider(provider_id)
        await gateway.storage.log_audit_event("provider", provider_id, "delete", None, "api")
        await gateway.refresh_config()
        return {"deleted": provider_id}

    @app.post("/v1/providers/{provider_id}/test")
    async def v1_test_provider(provider_id: str) -> dict:
        from voicegateway.core.registry import _PROVIDER_REGISTRY, create_provider

        pcfg = gateway.config.providers.get(provider_id)
        if pcfg is None:
            raise HTTPException(404, f"No provider '{provider_id}'")
        ptype = pcfg.get("provider_type", provider_id) if isinstance(pcfg, dict) else provider_id
        if ptype not in _PROVIDER_REGISTRY:
            return {"status": "failed", "message": f"Unknown type '{ptype}'"}
        try:
            inst = create_provider(ptype, pcfg if isinstance(pcfg, dict) else {})
            start = time.time()
            ok = await inst.health_check()
            latency_ms = int((time.time() - start) * 1000)
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed", "message": str(exc), "latency_ms": 0}
        return {"status": "ok" if ok else "failed", "latency_ms": latency_ms}

    # ------------------------------------------------------------------
    # CRUD — Models
    # ------------------------------------------------------------------

    @app.post("/v1/models")
    async def v1_create_model(body: dict[str, Any]) -> dict:
        if gateway.storage is None:
            raise HTTPException(400, "Storage not enabled")
        modality = body.get("modality", "")
        provider_id = body.get("provider_id", "")
        model_name = body.get("model_name", "")
        model_id = f"{provider_id}/{model_name}"

        if provider_id not in gateway.config.providers:
            raise HTTPException(400, f"Provider '{provider_id}' not configured")
        yaml_bucket = gateway.config.models.get(modality, {})
        if model_id in yaml_bucket:
            raise HTTPException(409, f"Model '{model_id}' already exists")

        await gateway.storage.upsert_managed_model(
            model_id=model_id,
            modality=modality,
            provider_id=provider_id,
            model_name=model_name,
            display_name=body.get("display_name"),
            default_language=body.get("default_language"),
            default_voice=body.get("default_voice"),
            extra_config=body.get("config"),
        )
        await gateway.storage.log_audit_event("model", model_id, "create", body, "api")
        await gateway.refresh_config()
        return {"model_id": model_id, "source": "db", "created": True}

    @app.delete("/v1/models/{model_id:path}")
    async def v1_delete_model(model_id: str, confirm: bool = Query(False)) -> dict:
        if gateway.storage is None:
            raise HTTPException(400, "Storage not enabled")
        managed = await gateway.storage.get_managed_model(model_id)
        if managed is None:
            for mm in gateway.config.models.values():
                if isinstance(mm, dict) and model_id in mm:
                    raise HTTPException(403, f"Model '{model_id}' is YAML-defined")
            raise HTTPException(404, f"No model '{model_id}'")
        if not confirm:
            return {"would_delete": {"model_id": model_id}}
        await gateway.storage.delete_managed_model(model_id)
        await gateway.storage.log_audit_event("model", model_id, "delete", None, "api")
        await gateway.refresh_config()
        return {"deleted": model_id}

    # ------------------------------------------------------------------
    # CRUD — Projects
    # ------------------------------------------------------------------

    @app.post("/v1/projects")
    async def v1_create_project(body: dict[str, Any]) -> dict:
        if gateway.storage is None:
            raise HTTPException(400, "Storage not enabled")
        pid = body.get("project_id", "")
        if pid in gateway.config.projects:
            raise HTTPException(409, f"Project '{pid}' already exists")
        await gateway.storage.upsert_managed_project(
            project_id=pid,
            name=body.get("name", pid),
            description=body.get("description", ""),
            daily_budget=float(body.get("daily_budget", 0.0)),
            budget_action=body.get("budget_action", "warn"),
            default_stack=body.get("default_stack"),
            stt_model=body.get("stt_model"),
            llm_model=body.get("llm_model"),
            tts_model=body.get("tts_model"),
            tags=body.get("tags"),
        )
        await gateway.storage.log_audit_event("project", pid, "create", body, "api")
        await gateway.refresh_config()
        return {"project_id": pid, "source": "db", "created": True}

    @app.patch("/v1/projects/{project_id}")
    async def v1_update_project(project_id: str, body: dict[str, Any]) -> dict:
        if gateway.storage is None:
            raise HTTPException(400, "Storage not enabled")
        managed = await gateway.storage.get_managed_project(project_id)
        if managed is None:
            raise HTTPException(404, f"No managed project '{project_id}'")
        await gateway.storage.upsert_managed_project(
            project_id=project_id,
            name=body.get("name", managed["name"]),
            description=body.get("description", managed.get("description", "")),
            daily_budget=float(body.get("daily_budget", managed.get("daily_budget", 0.0))),
            budget_action=body.get("budget_action", managed.get("budget_action", "warn")),
            default_stack=body.get("default_stack", managed.get("default_stack")),
            stt_model=body.get("stt_model", managed.get("stt_model")),
            llm_model=body.get("llm_model", managed.get("llm_model")),
            tts_model=body.get("tts_model", managed.get("tts_model")),
            tags=body.get("tags", managed.get("tags")),
        )
        await gateway.storage.log_audit_event("project", project_id, "update", body, "api")
        await gateway.refresh_config()
        return {"project_id": project_id, "updated": True}

    @app.delete("/v1/projects/{project_id}")
    async def v1_delete_project(project_id: str, confirm: bool = Query(False)) -> dict:
        if gateway.storage is None:
            raise HTTPException(400, "Storage not enabled")
        managed = await gateway.storage.get_managed_project(project_id)
        if managed is None:
            if project_id in gateway.config.projects:
                raise HTTPException(403, f"Project '{project_id}' is YAML-defined")
            raise HTTPException(404, f"No project '{project_id}'")
        if not confirm:
            return {"would_delete": {"project_id": project_id}}
        await gateway.storage.delete_managed_project(project_id)
        await gateway.storage.log_audit_event("project", project_id, "delete", None, "api")
        await gateway.refresh_config()
        return {"deleted": project_id}

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    @app.get("/v1/audit-log")
    async def v1_audit_log(
        limit: int = Query(50, ge=1, le=500),
        entity_type: str | None = Query(None),
        entity_id: str | None = Query(None),
        action: str | None = Query(None),
    ) -> list[dict]:
        if gateway.storage is None:
            return []
        return await gateway.storage.get_audit_log(
            limit=limit, entity_type=entity_type, entity_id=entity_id, action=action,
        )

    return app
