"""VoiceGateway HTTP API server."""

from __future__ import annotations

import dataclasses
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError

from voicegateway.core.auth import (
    AuthError,
    check_request,
    is_virtual_key_token,
    load_api_keys,
    resolve_cors_origins,
    verify_virtual_key,
)
from voicegateway.inference._session_context import set_tenant
from voicegateway.pricing import catalog
from voicegateway.repository import (
    guardrail_events_repository as guardrail_events,
)
from voicegateway.repository import (
    virtual_keys_repository as virtual_keys,
)
from voicegateway.schemas.guardrail_policy import (
    ACTIVE_GUARDRAIL_ACTIONS,
    GUARDRAIL_CATEGORIES,
    GUARDRAIL_CATEGORY_DESCRIPTIONS,
    GuardrailPolicy,
)
from voicegateway.utils.percentiles import quantile_label

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

logger = logging.getLogger(__name__)


def _parse_iso_date(value: str, *, end_of_day: bool) -> float:
    """Parse a YYYY-MM-DD string into a UTC POSIX timestamp."""
    try:
        d = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=f"invalid date {value!r}: expected YYYY-MM-DD",
        ) from e
    if end_of_day:
        d += timedelta(days=1)
    return d.timestamp()


def _attach_layered_stack(app: FastAPI, gateway: Gateway) -> None:
    """Wire the SQLAlchemy + dependency-injector layer onto the FastAPI app."""
    from dependency_injector import providers
    from fastapi.exceptions import RequestValidationError
    from sqlalchemy.exc import SQLAlchemyError

    from voicegateway.core.container import Container
    from voicegateway.core.exception_handlers import (
        auth_error_handler,
        duplicated_error_handler,
        global_exception_handler,
        not_found_error_handler,
        not_satisfiable_error_handler,
        permission_denied_error_handler,
        request_validation_exception_handler,
        sqlalchemy_exception_handler,
        unauthorized_error_handler,
        validation_error_handler,
    )
    from voicegateway.core.exceptions import (
        AuthError as LayeredAuthError,
    )
    from voicegateway.core.exceptions import (
        DuplicatedError,
        NotFoundError,
        NotSatisfiableError,
        PermissionDeniedError,
        UnauthorizedError,
    )
    from voicegateway.core.exceptions import (
        ValidationError as LayeredValidationError,
    )
    from voicegateway.server.routes import virtual_keys as virtual_keys_routes

    container = Container()
    container.config.override(providers.Object(gateway.config))
    container.wire(modules=container.wiring_config.modules)
    app.state.container = container

    app.include_router(virtual_keys_routes.router)

    app.add_exception_handler(
        RequestValidationError, request_validation_exception_handler
    )
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)
    for exc_class, handler in (
        (DuplicatedError, duplicated_error_handler),
        (LayeredAuthError, auth_error_handler),
        (NotFoundError, not_found_error_handler),
        (LayeredValidationError, validation_error_handler),
        (PermissionDeniedError, permission_denied_error_handler),
        (UnauthorizedError, unauthorized_error_handler),
        (NotSatisfiableError, not_satisfiable_error_handler),
    ):
        app.add_exception_handler(exc_class, handler)
    app.add_exception_handler(Exception, global_exception_handler)


def build_app(gateway: Gateway) -> FastAPI:
    """Build a FastAPI app bound to the given Gateway instance."""
    app = FastAPI(
        title="VoiceGateway API",
        version="0.6.0",
        description="HTTP API for VoiceGateway: cost tracking and reconciliation for LiveKit voice agents.",
    )

    _attach_layered_stack(app, gateway)

    api_keys = load_api_keys(gateway.config.auth)
    cors_origins = resolve_cors_origins(gateway.config.auth)
    if cors_origins == ["*"]:
        logger.warning(
            "CORS: allow_origins=['*']. Set auth.cors_origins in "
            "voicegw.yaml to restrict."
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Deprecation"],
    )

    def require_scope(scope: str):
        """Return a FastAPI dependency enforcing ``scope`` on a request."""

        async def _dep(request: Request) -> None:
            authorization = request.headers.get("Authorization")
            if is_virtual_key_token(authorization) and gateway.storage is not None:
                try:
                    db = await gateway.storage._ensure_initialized()
                    verified = await verify_virtual_key(authorization, db)
                except AuthError as exc:
                    raise HTTPException(
                        status_code=exc.status_code, detail=exc.message
                    ) from None
                await virtual_keys.mark_used(db, verified.id)
                request.state.virtual_key_id = verified.id
                request.state.virtual_key_tenant_id = verified.tenant_id
                if verified.tenant_id is not None:
                    set_tenant(verified.tenant_id)
                return

            try:
                check_request(
                    authorization,
                    scope,
                    api_keys,
                )
            except AuthError as exc:
                raise HTTPException(
                    status_code=exc.status_code, detail=exc.message
                ) from None

        return _dep

    def _guardrail_since(days: int) -> str:
        days = max(1, min(days, 365))
        return (datetime.now(tz=UTC) - timedelta(days=days)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    write_dep = Depends(require_scope("write"))

    started_at = time.time()

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "uptime_seconds": round(time.time() - started_at, 1),
            "version": "0.5.0",
        }

    @app.get("/v1/status")
    async def v1_status() -> dict:
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

        from voicegateway.pricing import llm as _llm_pricing
        from voicegateway.pricing import stt as _stt_pricing
        from voicegateway.pricing import tts as _tts_pricing

        pricing = {
            "llm": {"source": _llm_pricing.PRICING_SOURCE},
            "stt": {
                "source": _stt_pricing.PRICING_SOURCE,
                "oldest_entry_date": _stt_pricing._oldest_pricing_date().isoformat(),
            },
            "tts": {
                "source": _tts_pricing.PRICING_SOURCE,
                "oldest_entry_date": _tts_pricing._oldest_pricing_date().isoformat(),
            },
        }
        return {
            "providers": providers,
            "model_count": sum(
                len(v) for v in cfg.models.values() if isinstance(v, dict)
            ),
            "project_count": len(cfg.projects),
            "pricing": pricing,
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
        response: Response,
        period: str | None = Query(None),
        project: str | None = Query(None),
        per_modality: bool = Query(False),
        include_pricing_source: bool = Query(True),
        start: str | None = Query(None),
        end: str | None = Query(None),
    ) -> dict:

        pricing_sources = {
            modality: catalog.pricing_source(modality)
            for modality in ("llm", "stt", "tts")
        }

        period_explicit = period is not None
        effective_period = period if period is not None else "today"

        if period_explicit and (start or end):
            response.headers["Deprecation"] = (
                "period parameter is ignored when start/end are "
                "provided. Drop period from new-API calls."
            )

        start_ts = _parse_iso_date(start, end_of_day=False) if start else None
        end_ts = _parse_iso_date(end, end_of_day=True) if end else None
        if gateway.storage is None:
            empty: dict[str, Any] = {
                "period": effective_period,
                "project": project,
                "total": 0.0,
                "by_provider": {},
                "by_model": {},
                "by_project": {},
                "pricing_sources": pricing_sources,
            }
            if per_modality:
                empty["by_modality"] = {}
            return empty
        summary = await gateway.storage.get_cost_summary(
            effective_period,
            project=project,
            include_pricing_source=include_pricing_source,
            start_ts=start_ts,
            end_ts=end_ts,
        )
        if project is None:
            summary["by_project"] = await gateway.storage.get_cost_by_project(
                effective_period, start_ts=start_ts, end_ts=end_ts
            )
        else:
            summary["by_project"] = {}
        if per_modality:
            summary["by_modality"] = await gateway.storage.get_cost_by_modality(
                effective_period,
                project=project,
                start_ts=start_ts,
                end_ts=end_ts,
            )
        summary["pricing_sources"] = pricing_sources
        return summary

    @app.get("/v1/latency")
    async def v1_latency(
        period: str = Query("today"),
        project: str | None = Query(None),
    ) -> dict:
        if gateway.storage is None:
            return {}
        pcts = gateway.config.latency.get("percentiles") or [50.0, 95.0, 99.0]
        return await gateway.storage.get_latency_stats(
            period, project=project, percentiles=pcts
        )

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
            costs_today = await gateway.storage.get_cost_summary(
                "today", project=project_id
            )
            data["costs_today"] = costs_today
            today_spend = costs_today.get("total", 0.0)
            data["today_spend"] = today_spend
            enforcer = gateway._budget_enforcer
            data["budget_status"] = enforcer.get_budget_status(project_id, today_spend)
        return data

    @app.get("/v1/projects/{project_id}/guardrails")
    async def v1_project_guardrails(project_id: str) -> dict[str, Any]:
        pcfg = gateway.config.get_project(project_id)
        if pcfg is None:
            raise HTTPException(404, f"project not found: {project_id}")
        return {
            "project_id": project_id,
            "policy": pcfg.guardrails.to_storage_dict(),
            "categories": [
                {
                    "id": category,
                    "description": GUARDRAIL_CATEGORY_DESCRIPTIONS[category],
                }
                for category in GUARDRAIL_CATEGORY_DESCRIPTIONS
            ],
        }

    @app.post("/v1/projects/{project_id}/guardrails", dependencies=[write_dep])
    async def v1_update_project_guardrails(
        project_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        if gateway.storage is None:
            raise HTTPException(400, "Storage not enabled")
        pcfg = gateway.config.get_project(project_id)
        if pcfg is None:
            raise HTTPException(404, f"project not found: {project_id}")
        try:
            policy = GuardrailPolicy.from_raw(body)
        except (ValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await gateway.storage.set_managed_project_guardrails(
            project_id=project_id,
            policy=policy.to_storage_dict(),
            name=pcfg.name,
            description=pcfg.description,
            daily_budget=pcfg.daily_budget,
            budget_action=pcfg.budget_action,
            default_stack=pcfg.default_stack,
            tags=list(pcfg.tags),
        )
        await gateway.storage.log_audit_event(
            "project", project_id, "guardrails_update", policy.to_storage_dict(), "api"
        )
        await gateway.refresh_config()
        refreshed = gateway.config.get_project(project_id)
        return {
            "project_id": project_id,
            "policy": (
                refreshed.guardrails.to_storage_dict()
                if refreshed is not None
                else policy.to_storage_dict()
            ),
        }

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

    @app.get("/v1/sessions")
    async def v1_sessions(
        limit: int = Query(100, ge=1, le=1000),
        project: str | None = Query(None),
        order_by: str = Query(
            "started_at_desc",
            pattern="^(started_at_desc|started_at_asc|cost_desc|cost_asc)$",
        ),
    ) -> list[dict]:
        """Return recent voice sessions, ordered per ``order_by``."""
        if gateway.storage is None:
            return []
        return await gateway.storage.list_sessions(
            limit=limit, project=project, order_by=order_by
        )

    @app.get("/v1/sessions/{session_id}")
    async def v1_session_detail(session_id: str) -> dict:
        """Return one session by id with a per-modality cost breakdown."""
        if gateway.storage is None:
            raise HTTPException(
                status_code=404, detail=f"Session '{session_id}' not found"
            )
        row = await gateway.storage.get_session(session_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Session '{session_id}' not found"
            )
        return row

    @app.get("/v1/guardrails/events")
    async def v1_guardrail_events(
        days: int = Query(7, ge=1, le=365),
        project: str | None = Query(None),
        tenant: str | None = Query(None),
        category: str | None = Query(None),
        action: str | None = Query(None),
        event_type: str | None = Query(None, pattern="^(fired|bypassed)$"),
        limit: int = Query(100, ge=1, le=1000),
    ) -> dict[str, Any]:
        if gateway.storage is None:
            return {"events": [], "filter": {"project": project, "tenant": tenant}}
        _validate_guardrail_event_filter(category=category, action=action)
        db = await gateway.storage._ensure_initialized()
        try:
            rows = await guardrail_events.list_events(
                db,
                since=_guardrail_since(days),
                project=project,
                tenant=tenant,
                category=category,
                action=action,
                event_type=event_type,
                limit=limit,
            )
        finally:
            await db.close()
        return {
            "events": [dataclasses.asdict(row) for row in rows],
            "filter": {
                "days": days,
                "project": project,
                "tenant": tenant,
                "category": category,
                "action": action,
                "event_type": event_type,
            },
        }

    @app.get("/v1/guardrails/aggregate")
    async def v1_guardrail_aggregate(
        days: int = Query(7, ge=1, le=365),
        project: str | None = Query(None),
        tenant: str | None = Query(None),
        category: str | None = Query(None),
    ) -> dict[str, Any]:
        if gateway.storage is None:
            return {"counts": [], "top_sessions": []}
        _validate_guardrail_event_filter(category=category, action=None)
        db = await gateway.storage._ensure_initialized()
        try:
            since = _guardrail_since(days)
            counts = await guardrail_events.aggregate_counts(
                db, since=since, project=project, tenant=tenant
            )
            top_sessions = (
                await guardrail_events.top_sessions_by_category(
                    db,
                    category=category,
                    since=since,
                    project=project,
                    tenant=tenant,
                )
                if category
                else []
            )
        finally:
            await db.close()
        return {
            "counts": [dataclasses.asdict(row) for row in counts],
            "top_sessions": [dataclasses.asdict(row) for row in top_sessions],
            "filter": {
                "days": days,
                "project": project,
                "tenant": tenant,
                "category": category,
            },
        }

    def _validate_guardrail_event_filter(
        *, category: str | None, action: str | None
    ) -> None:
        if category is not None and category not in GUARDRAIL_CATEGORIES:
            allowed = ", ".join(GUARDRAIL_CATEGORIES)
            raise HTTPException(
                status_code=400,
                detail=f"unknown guardrail category: {category}; allowed: {allowed}",
            )
        if action is not None and action not in ACTIVE_GUARDRAIL_ACTIONS:
            allowed = ", ".join(ACTIVE_GUARDRAIL_ACTIONS)
            raise HTTPException(
                status_code=400,
                detail=f"unknown guardrail action: {action}; allowed: {allowed}",
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
            result.append(
                {
                    "provider_id": name,
                    "source": source,
                    "api_key_masked": mask(api_key)
                    if source != "db"
                    else mask(api_key),
                    "base_url": pcfg.get("base_url")
                    if isinstance(pcfg, dict)
                    else None,
                }
            )
        return {"providers": result}

    @app.post("/v1/providers", dependencies=[write_dep])
    async def v1_create_provider(body: dict[str, Any]) -> dict:
        from voicegateway.core.crypto import mask
        from voicegateway.core.registry import _PROVIDER_REGISTRY

        pid = body.get("provider_id", "")
        ptype = body.get("provider_type", pid)
        api_key = body.get("api_key", "")
        base_url = body.get("base_url")
        project = body.get("project")

        if not pid or not isinstance(pid, str):
            raise HTTPException(400, "provider_id is required and must be a string")
        if ptype not in _PROVIDER_REGISTRY:
            raise HTTPException(400, f"Unknown provider_type '{ptype}'")
        if pid in gateway.config.providers:
            is_managed = (
                isinstance(gateway.config.providers[pid], dict)
                and gateway.config.providers[pid].get("_source") == "db"
            )
            if not is_managed:
                raise HTTPException(409, f"Provider '{pid}' already exists in YAML")

        if project:
            existing_project = gateway.config.projects.get(project)
            if existing_project is not None and ptype in existing_project.providers:
                yaml_entry = existing_project.providers[ptype]
                yaml_pinned = (
                    not isinstance(yaml_entry, dict)
                    or yaml_entry.get("_source") != "db"
                )
                if yaml_pinned:
                    raise HTTPException(
                        409,
                        f"Provider '{ptype}' is already pinned in YAML at "
                        f"projects.{project}.providers.{ptype}; "
                        "remove it from voicegw.yaml before creating a "
                        "managed override.",
                    )
        if gateway.storage is None:
            raise HTTPException(400, "Storage not enabled")

        await gateway.storage.upsert_managed_provider(
            pid, ptype, api_key, base_url, project=project
        )
        audit_body = {**body, "api_key": "<redacted>"} if "api_key" in body else body
        await gateway.storage.log_audit_event(
            "provider", pid, "create", audit_body, "api"
        )
        await gateway.refresh_config()

        return {
            "provider_id": pid,
            "source": "db",
            "api_key_masked": mask(api_key),
            "project": project,
        }

    @app.patch("/v1/providers/{provider_id}", dependencies=[write_dep])
    async def v1_update_provider(provider_id: str, body: dict[str, Any]) -> dict:
        from voicegateway.core.registry import _PROVIDER_REGISTRY

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
        if ptype not in _PROVIDER_REGISTRY:
            raise HTTPException(400, f"Unknown provider_type '{ptype}'")

        project = body.get("project", existing.get("project"))

        await gateway.storage.upsert_managed_provider(
            provider_id, ptype, api_key, base_url, project=project
        )
        audit_body = {**body, "api_key": "<redacted>"} if "api_key" in body else body
        await gateway.storage.log_audit_event(
            "provider", provider_id, "update", audit_body, "api"
        )
        await gateway.refresh_config()
        return {"provider_id": provider_id, "updated": True}

    @app.delete("/v1/providers/{provider_id}", dependencies=[write_dep])
    async def v1_delete_provider(
        provider_id: str,
        confirm: bool = Query(False),
    ) -> dict:
        if gateway.storage is None:
            raise HTTPException(400, "Storage not enabled")
        managed = await gateway.storage.get_managed_provider(provider_id)
        if managed is None:
            if provider_id in gateway.config.providers:
                raise HTTPException(
                    403,
                    f"Provider '{provider_id}' is YAML-defined and cannot be deleted",
                )
            raise HTTPException(404, f"No provider '{provider_id}'")

        if not confirm:
            return {"would_delete": {"provider_id": provider_id}}
        await gateway.storage.delete_managed_provider(provider_id)
        await gateway.storage.log_audit_event(
            "provider", provider_id, "delete", None, "api"
        )
        await gateway.refresh_config()
        return {"deleted": provider_id}

    async def _resolve_test_target(
        provider_id: str,
    ) -> tuple[str | None, dict[str, Any] | None]:
        """Return ``(provider_type, provider_config)`` for the test path."""
        cfg = gateway.config

        if provider_id in cfg.providers:
            pcfg = cfg.providers[provider_id]
            if isinstance(pcfg, dict):
                if pcfg.get("_source") != "db":
                    return provider_id, dict(pcfg)

        if gateway.storage is not None:
            row = await gateway.storage.get_managed_provider(provider_id)
            if row is not None:
                from voicegateway.core.crypto import decrypt

                return row["provider_type"], {
                    "api_key": decrypt(row.get("api_key_encrypted", "")),
                    "base_url": row.get("base_url"),
                    **(row.get("extra_config") or {}),
                }

        if ":" in provider_id:
            project, _, provider_type = provider_id.partition(":")
            project_cfg = cfg.projects.get(project)
            if project_cfg is not None and provider_type in project_cfg.providers:
                return provider_type, dict(project_cfg.providers[provider_type])

        return None, None

    @app.post("/v1/providers/{provider_id}/test", dependencies=[write_dep])
    async def v1_test_provider(provider_id: str) -> dict:
        import asyncio as _asyncio
        import logging as _logging

        from voicegateway.core.registry import _PROVIDER_REGISTRY, create_provider

        _test_log = _logging.getLogger(__name__)
        ptype, pcfg = await _resolve_test_target(provider_id)
        if pcfg is None:
            raise HTTPException(404, f"No provider '{provider_id}'")
        if ptype not in _PROVIDER_REGISTRY:
            return {
                "status": "failed",
                "message": f"Unknown type '{ptype}'",
                "latency_ms": 0,
            }
        try:
            inst = create_provider(ptype, pcfg)
            start = time.time()
            ok = await _asyncio.wait_for(inst.health_check(), timeout=10.0)
            latency_ms = int((time.time() - start) * 1000)
        except TimeoutError:
            return {
                "status": "failed",
                "message": "Provider health check timed out",
                "latency_ms": 10000,
            }
        except Exception as exc:  # noqa: BLE001
            _test_log.warning("Provider test for '%s' failed: %s", provider_id, exc)
            return {
                "status": "failed",
                "message": "Provider health check failed",
                "latency_ms": 0,
            }
        return {"status": "ok" if ok else "failed", "latency_ms": latency_ms}

    @app.post("/v1/providers/test", dependencies=[write_dep])
    async def v1_test_provider_stateless(body: dict[str, Any]) -> dict:
        """Stateless health check: takes provider_type + api_key + base_url,"""
        import asyncio as _asyncio
        import logging as _logging

        from voicegateway.core.registry import _PROVIDER_REGISTRY, create_provider

        _stateless_log = _logging.getLogger(__name__)
        ptype = body.get("provider_type", "")
        if ptype not in _PROVIDER_REGISTRY:
            return {
                "status": "failed",
                "message": f"Unknown provider_type '{ptype}'",
                "latency_ms": 0,
            }
        cfg: dict[str, Any] = {
            "api_key": body.get("api_key", ""),
            "base_url": body.get("base_url"),
        }
        try:
            inst = create_provider(ptype, cfg)
            start = time.time()
            ok = await _asyncio.wait_for(inst.health_check(), timeout=10.0)
            latency_ms = int((time.time() - start) * 1000)
        except TimeoutError:
            return {
                "status": "failed",
                "message": "Provider health check timed out",
                "latency_ms": 10000,
            }
        except Exception as exc:  # noqa: BLE001
            _stateless_log.warning(
                "Stateless provider test for '%s' failed: %s", ptype, exc
            )
            return {
                "status": "failed",
                "message": "Provider health check failed",
                "latency_ms": 0,
            }
        return {"status": "ok" if ok else "failed", "latency_ms": latency_ms}

    # ------------------------------------------------------------------
    # CRUD — Models
    # ------------------------------------------------------------------

    @app.post("/v1/models", dependencies=[write_dep])
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

    @app.delete("/v1/models/{model_id:path}", dependencies=[write_dep])
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

    @app.post("/v1/projects", dependencies=[write_dep])
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

    @app.patch("/v1/projects/{project_id}", dependencies=[write_dep])
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
            daily_budget=float(
                body.get("daily_budget", managed.get("daily_budget", 0.0))
            ),
            budget_action=body.get(
                "budget_action", managed.get("budget_action", "warn")
            ),
            default_stack=body.get("default_stack", managed.get("default_stack")),
            stt_model=body.get("stt_model", managed.get("stt_model")),
            llm_model=body.get("llm_model", managed.get("llm_model")),
            tts_model=body.get("tts_model", managed.get("tts_model")),
            tags=body.get("tags", managed.get("tags")),
        )
        await gateway.storage.log_audit_event(
            "project", project_id, "update", body, "api"
        )
        await gateway.refresh_config()
        return {"project_id": project_id, "updated": True}

    @app.delete("/v1/projects/{project_id}", dependencies=[write_dep])
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
        await gateway.storage.log_audit_event(
            "project", project_id, "delete", None, "api"
        )
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
            limit=limit,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
        )

    return app
