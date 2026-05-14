"""SQLite storage facade aggregating per-domain service objects."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database
from voicegateway.models.request_model import RequestRecord
from voicegateway.services.cost_service import CostService
from voicegateway.services.latency_service import LatencyService
from voicegateway.services.managed_config_service import ManagedConfigService
from voicegateway.services.request_log_service import RequestLogService
from voicegateway.services.session_service import SessionService

_DEFAULT_PERCENTILES: list[float] = [50.0, 95.0, 99.0]

_logger = logging.getLogger(__name__)


class StorageService:
    """Aggregates per-domain services over one SQLite database."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        cfg = GatewayConfig(cost_tracking={"db_path": str(self._db_path)})
        self._conn = Database(cfg)
        self._initialized = False
        # Serialize concurrent _ensure_initialized calls so alembic's
        # bootstrap of the alembic_version table never races itself.
        self._init_lock = asyncio.Lock()
        self._request_log_service = RequestLogService(self._conn)
        self._cost_service = CostService(self._conn)
        self._latency_service = LatencyService(self._conn)
        self._session_service = SessionService(self._conn)
        self._managed_config_service = ManagedConfigService(self._conn)

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:  # double-checked after lock
                return
            await self._conn.run_migrations()
            self._initialized = True

    async def aclose(self) -> None:
        """Dispose the underlying engine."""
        await self._conn.dispose()

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    async def log_audit_event(
        self,
        entity_type: str,
        entity_id: str,
        action: str,
        changes: dict[str, Any] | None = None,
        source: str = "api",
    ) -> None:
        """Delegate to RequestLogService.log_audit_event."""
        await self._ensure_initialized()
        await self._request_log_service.log_audit_event(
            entity_type, entity_id, action, changes, source
        )

    async def get_audit_log(
        self,
        limit: int = 50,
        entity_type: str | None = None,
        entity_id: str | None = None,
        action: str | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate to RequestLogService.get_audit_log."""
        await self._ensure_initialized()
        return await self._request_log_service.get_audit_log(
            limit, entity_type, entity_id, action
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def log_request(self, record: RequestRecord) -> None:
        """Delegate to RequestLogService.log_request."""
        await self._ensure_initialized()
        await self._request_log_service.log_request(record)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_cost_summary(
        self,
        period: str = "today",
        project: str | None = None,
        include_pricing_source: bool = False,
        start_ts: float | None = None,
        end_ts: float | None = None,
        tenant: str | None = None,
    ) -> dict[str, Any]:
        """Delegate to CostService.get_summary."""
        await self._ensure_initialized()
        return await self._cost_service.get_summary(
            period=period,
            project=project,
            include_pricing_source=include_pricing_source,
            start_ts=start_ts,
            end_ts=end_ts,
            tenant=tenant,
        )

    async def get_cost_by_project(
        self,
        period: str = "today",
        start_ts: float | None = None,
        end_ts: float | None = None,
        tenant: str | None = None,
    ) -> dict[str, Any]:
        """Delegate to CostService.get_by_project."""
        await self._ensure_initialized()
        return await self._cost_service.get_by_project(
            period=period, start_ts=start_ts, end_ts=end_ts, tenant=tenant
        )

    async def get_cost_by_modality(
        self,
        period: str = "today",
        project: str | None = None,
        start_ts: float | None = None,
        end_ts: float | None = None,
    ) -> dict[str, Any]:
        """Delegate to CostService.get_by_modality."""
        await self._ensure_initialized()
        return await self._cost_service.get_by_modality(
            period=period, project=project, start_ts=start_ts, end_ts=end_ts
        )

    async def get_latency_stats(
        self,
        period: str = "today",
        project: str | None = None,
        percentiles: list[float] | None = None,
        tenant: str | None = None,
    ) -> dict[str, Any]:
        """Delegate to LatencyService.get_stats."""
        await self._ensure_initialized()
        return await self._latency_service.get_stats(
            period=period,
            project=project,
            percentiles=percentiles,
            tenant=tenant,
        )

    async def get_latency_samples(
        self,
        period: str = "today",
        project: str | None = None,
        modality: str | None = None,
    ) -> tuple[list[float], list[float]]:
        """Delegate to LatencyService.get_samples."""
        await self._ensure_initialized()
        return await self._latency_service.get_samples(
            period=period, project=project, modality=modality
        )

    async def get_requests_in_window(
        self,
        start_ts: float | None = None,
        end_ts: float | None = None,
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate to RequestLogService.get_requests_in_window."""
        await self._ensure_initialized()
        return await self._request_log_service.get_requests_in_window(
            start_ts, end_ts, project
        )

    async def get_recent_requests(
        self,
        limit: int = 100,
        modality: str | None = None,
        project: str | None = None,
        tenant: str | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate to RequestLogService.get_recent_requests."""
        await self._ensure_initialized()
        return await self._request_log_service.get_recent_requests(
            limit, modality, project, tenant
        )

    async def get_project_stats(self, project: str) -> dict[str, Any]:
        """Delegate to CostService.get_project_stats."""
        await self._ensure_initialized()
        return await self._cost_service.get_project_stats(project)

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    async def list_sessions(
        self,
        limit: int = 100,
        project: str | None = None,
        order_by: str = "started_at_desc",
        tenant: str | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate to SessionService.list_sessions."""
        await self._ensure_initialized()
        return await self._session_service.list_sessions(
            limit=limit, project=project, order_by=order_by, tenant=tenant
        )

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Delegate to SessionService.get_session."""
        await self._ensure_initialized()
        return await self._session_service.get_session(session_id)

    async def finalize_session_metrics(self, session_id: str) -> None:
        """Delegate to SessionService.finalize_metrics."""
        await self._ensure_initialized()
        await self._session_service.finalize_metrics(session_id)

    async def finalize_session_replay(self, session_id: str) -> None:
        """Delegate to SessionService.finalize_replay."""
        await self._ensure_initialized()
        await self._session_service.finalize_replay(session_id)

    # ------------------------------------------------------------------
    # Managed providers / models / projects
    # ------------------------------------------------------------------

    # Managed providers
    async def list_managed_providers(self) -> list[dict[str, Any]]:
        """Delegate to ManagedConfigService.list_providers."""
        await self._ensure_initialized()
        return await self._managed_config_service.list_providers()

    async def get_managed_provider(self, provider_id: str) -> dict[str, Any] | None:
        """Delegate to ManagedConfigService.get_provider."""
        await self._ensure_initialized()
        return await self._managed_config_service.get_provider(provider_id)

    async def upsert_managed_provider(
        self,
        provider_id: str,
        provider_type: str,
        api_key: str,
        base_url: str | None = None,
        extra_config: dict[str, Any] | None = None,
        project: str | None = None,
    ) -> None:
        """Delegate to ManagedConfigService.upsert_provider."""
        await self._ensure_initialized()
        await self._managed_config_service.upsert_provider(
            provider_id,
            provider_type,
            api_key,
            base_url=base_url,
            extra_config=extra_config,
            project=project,
        )

    async def delete_managed_provider(self, provider_id: str) -> bool:
        """Delegate to ManagedConfigService.delete_provider."""
        await self._ensure_initialized()
        return await self._managed_config_service.delete_provider(provider_id)

    async def rotate_managed_credentials(
        self, *, time_now: float | None = None
    ) -> dict[str, Any]:
        """Delegate to ManagedConfigService.rotate_credentials."""
        await self._ensure_initialized()
        return await self._managed_config_service.rotate_credentials(time_now=time_now)

    # Managed models
    async def list_managed_models(self) -> list[dict[str, Any]]:
        """Delegate to ManagedConfigService.list_models."""
        await self._ensure_initialized()
        return await self._managed_config_service.list_models()

    async def get_managed_model(self, model_id: str) -> dict[str, Any] | None:
        """Delegate to ManagedConfigService.get_model."""
        await self._ensure_initialized()
        return await self._managed_config_service.get_model(model_id)

    async def upsert_managed_model(
        self,
        model_id: str,
        modality: str,
        provider_id: str,
        model_name: str,
        display_name: str | None = None,
        default_language: str | None = None,
        default_voice: str | None = None,
        extra_config: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> None:
        """Delegate to ManagedConfigService.upsert_model."""
        await self._ensure_initialized()
        await self._managed_config_service.upsert_model(
            model_id,
            modality,
            provider_id,
            model_name,
            display_name=display_name,
            default_language=default_language,
            default_voice=default_voice,
            extra_config=extra_config,
            enabled=enabled,
        )

    async def delete_managed_model(self, model_id: str) -> bool:
        """Delegate to ManagedConfigService.delete_model."""
        await self._ensure_initialized()
        return await self._managed_config_service.delete_model(model_id)

    # Managed projects
    async def list_managed_projects(self) -> list[dict[str, Any]]:
        """Delegate to ManagedConfigService.list_projects."""
        await self._ensure_initialized()
        return await self._managed_config_service.list_projects()

    async def get_managed_project(self, project_id: str) -> dict[str, Any] | None:
        """Delegate to ManagedConfigService.get_project."""
        await self._ensure_initialized()
        return await self._managed_config_service.get_project(project_id)

    async def upsert_managed_project(
        self,
        project_id: str,
        name: str,
        description: str = "",
        daily_budget: float = 0.0,
        budget_action: str = "warn",
        default_stack: str | None = None,
        stt_model: str | None = None,
        llm_model: str | None = None,
        tts_model: str | None = None,
        tags: list[str] | None = None,
        branding: dict[str, Any] | None = None,
        guardrail_policy: dict[str, Any] | None = None,
    ) -> None:
        """Delegate to ManagedConfigService.upsert_project."""
        await self._ensure_initialized()
        await self._managed_config_service.upsert_project(
            project_id,
            name,
            description=description,
            daily_budget=daily_budget,
            budget_action=budget_action,
            default_stack=default_stack,
            stt_model=stt_model,
            llm_model=llm_model,
            tts_model=tts_model,
            tags=tags,
            branding=branding,
            guardrail_policy=guardrail_policy,
        )

    async def set_managed_project_guardrails(
        self,
        *,
        project_id: str,
        policy: dict[str, Any] | None,
        name: str,
        description: str = "",
        daily_budget: float = 0.0,
        budget_action: str = "warn",
        default_stack: str | None = None,
        stt_model: str | None = None,
        llm_model: str | None = None,
        tts_model: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """Delegate to ManagedConfigService.set_project_guardrails."""
        await self._ensure_initialized()
        await self._managed_config_service.set_project_guardrails(
            project_id=project_id,
            policy=policy,
            name=name,
            description=description,
            daily_budget=daily_budget,
            budget_action=budget_action,
            default_stack=default_stack,
            stt_model=stt_model,
            llm_model=llm_model,
            tts_model=tts_model,
            tags=tags,
        )

    async def delete_managed_project(self, project_id: str) -> bool:
        """Delegate to ManagedConfigService.delete_project."""
        await self._ensure_initialized()
        return await self._managed_config_service.delete_project(project_id)
