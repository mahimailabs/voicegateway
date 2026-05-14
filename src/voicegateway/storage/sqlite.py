"""SQLite storage backend for request logs, projects, and cost tracking."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import aiosqlite

from voicegateway.models.request_model import RequestRecord
from voicegateway.repository import cost_repository as cost_repo
from voicegateway.schemas.guardrail_policy_schema import GuardrailPolicy
from voicegateway.services.cost_service import CostService
from voicegateway.services.latency_service import LatencyService
from voicegateway.services.request_log_service import RequestLogService
from voicegateway.services.session_service import SessionService
from voicegateway.storage.connection import ConnectionManager
from voicegateway.storage.migrator import initialize as _initialize_schema

_DEFAULT_PERCENTILES: list[float] = [50.0, 95.0, 99.0]

_logger = logging.getLogger(__name__)


class SQLiteStorage:
    """SQLite storage for request logs, costs, and latency metrics."""

    def __init__(self, db_path: str | Path) -> None:
        self._conn = ConnectionManager(db_path)
        self._request_log_service = RequestLogService(self._conn)
        self._cost_service = CostService(self._conn)
        self._latency_service = LatencyService(self._conn)
        self._session_service = SessionService(self._conn)

    @property
    def _db_path(self) -> Path:
        """Back-compat shim for callers reading the file path directly."""
        return self._conn.db_path

    @property
    def _initialized(self) -> bool:
        """Back-compat shim used by a few tests to assert post-init state."""
        return self._conn.is_initialized

    async def _ensure_initialized(self) -> aiosqlite.Connection:
        db = await self._conn.connect()
        await _initialize_schema(db, self._conn)
        return db

    async def aclose(self) -> None:
        """Close any raw connections still owned by this storage instance."""
        await self._conn.aclose()

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

    @staticmethod
    def _period_since(period: str) -> float:
        """Back-compat shim — call cost_repository.period_since instead."""
        return cost_repo.period_since(period)

    @staticmethod
    def _resolve_window(
        period: str = "today",
        start_ts: float | None = None,
        end_ts: float | None = None,
    ) -> tuple[float, float | None]:
        """Back-compat shim — call cost_repository.resolve_window instead."""
        return cost_repo.resolve_window(period, start_ts, end_ts)

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

    @staticmethod
    def _row_to_session(row: Any) -> dict[str, Any]:
        """Back-compat shim — call session_repository.row_to_session instead."""
        from voicegateway.repository import session_repository as _session_repo

        return _session_repo.row_to_session(row)

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

    async def list_managed_providers(self) -> list[dict[str, Any]]:
        """Return managed providers. api_key_encrypted is ciphertext; callers must decrypt."""
        db = await self._ensure_initialized()
        try:
            cursor = await db.execute(
                "SELECT provider_id, provider_type, api_key_encrypted, base_url, "
                "extra_config, created_at, updated_at, project FROM managed_providers "
                "ORDER BY created_at ASC"
            )
            rows = []
            async for row in cursor:
                rows.append(
                    {
                        "provider_id": row[0],
                        "provider_type": row[1],
                        "api_key_encrypted": row[2],
                        "base_url": row[3],
                        "extra_config": json.loads(row[4] or "{}"),
                        "created_at": row[5],
                        "updated_at": row[6],
                        "project": row[7],
                    }
                )
            return rows
        finally:
            await db.close()

    async def get_managed_provider(self, provider_id: str) -> dict[str, Any] | None:
        """Return a managed provider. api_key_encrypted is ciphertext."""
        db = await self._ensure_initialized()
        try:
            cursor = await db.execute(
                "SELECT provider_id, provider_type, api_key_encrypted, base_url, "
                "extra_config, created_at, updated_at, project FROM managed_providers "
                "WHERE provider_id = ?",
                (provider_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return {
                "provider_id": row[0],
                "provider_type": row[1],
                "api_key_encrypted": row[2],
                "base_url": row[3],
                "extra_config": json.loads(row[4] or "{}"),
                "created_at": row[5],
                "updated_at": row[6],
                "project": row[7],
            }
        finally:
            await db.close()

    async def upsert_managed_provider(
        self,
        provider_id: str,
        provider_type: str,
        api_key: str,
        base_url: str | None = None,
        extra_config: dict[str, Any] | None = None,
        project: str | None = None,
    ) -> None:
        from voicegateway.core.crypto import encrypt

        db = await self._ensure_initialized()
        try:
            now = time.time()
            encrypted_key = encrypt(api_key)
            await db.execute(
                """INSERT INTO managed_providers
                       (provider_id, provider_type, api_key_encrypted, base_url,
                        extra_config, created_at, updated_at, project)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(provider_id) DO UPDATE SET
                       provider_type=excluded.provider_type,
                       api_key_encrypted=excluded.api_key_encrypted,
                       base_url=excluded.base_url,
                       extra_config=excluded.extra_config,
                       updated_at=excluded.updated_at,
                       project=excluded.project""",
                (
                    provider_id,
                    provider_type,
                    encrypted_key,
                    base_url,
                    json.dumps(extra_config or {}),
                    now,
                    now,
                    project,
                ),
            )
            await db.commit()
        finally:
            await db.close()

    async def delete_managed_provider(self, provider_id: str) -> bool:
        db = await self._ensure_initialized()
        try:
            cursor = await db.execute(
                "DELETE FROM managed_providers WHERE provider_id = ?", (provider_id,)
            )
            await db.commit()
            return (cursor.rowcount or 0) > 0
        finally:
            await db.close()

    async def rotate_managed_credentials(
        self, *, time_now: float | None = None
    ) -> dict[str, Any]:
        """Re-encrypt every managed_providers row under the current"""
        from voicegateway.core.crypto import rotate_token

        now = time_now if time_now is not None else time.time()
        rotated = 0
        skipped_empty = 0
        failed: list[str] = []

        db = await self._ensure_initialized()
        try:
            cursor = await db.execute(
                "SELECT provider_id, api_key_encrypted FROM managed_providers"
            )
            rows = await cursor.fetchall()
            for provider_id, ciphertext in rows:
                if not ciphertext:
                    skipped_empty += 1
                    continue
                try:
                    new_ciphertext = rotate_token(ciphertext)
                except ValueError:
                    failed.append(provider_id)
                    continue
                if new_ciphertext == ciphertext:
                    # MultiFernet.rotate is non-deterministic (Fernet
                    # uses a random IV), so this branch is effectively
                    # unreachable. Guard anyway: if it fires, we still
                    # bump updated_at so the dashboard reflects the
                    # rotation attempt.
                    pass
                await db.execute(
                    "UPDATE managed_providers SET api_key_encrypted = ?, "
                    "updated_at = ? WHERE provider_id = ?",
                    (new_ciphertext, now, provider_id),
                )
                rotated += 1
            await db.commit()
        finally:
            await db.close()

        return {"rotated": rotated, "skipped_empty": skipped_empty, "failed": failed}

    # Managed models

    async def list_managed_models(self) -> list[dict[str, Any]]:
        db = await self._ensure_initialized()
        try:
            cursor = await db.execute(
                "SELECT model_id, modality, provider_id, model_name, display_name, "
                "default_language, default_voice, extra_config, enabled, "
                "created_at, updated_at FROM managed_models ORDER BY created_at ASC"
            )
            rows = []
            async for row in cursor:
                rows.append(
                    {
                        "model_id": row[0],
                        "modality": row[1],
                        "provider_id": row[2],
                        "model_name": row[3],
                        "display_name": row[4],
                        "default_language": row[5],
                        "default_voice": row[6],
                        "extra_config": json.loads(row[7] or "{}"),
                        "enabled": bool(row[8]),
                        "created_at": row[9],
                        "updated_at": row[10],
                    }
                )
            return rows
        finally:
            await db.close()

    async def get_managed_model(self, model_id: str) -> dict[str, Any] | None:
        for m in await self.list_managed_models():
            if m["model_id"] == model_id:
                return m
        return None

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
        db = await self._ensure_initialized()
        try:
            now = time.time()
            await db.execute(
                """INSERT INTO managed_models
                       (model_id, modality, provider_id, model_name, display_name,
                        default_language, default_voice, extra_config, enabled,
                        created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(model_id) DO UPDATE SET
                       modality=excluded.modality,
                       provider_id=excluded.provider_id,
                       model_name=excluded.model_name,
                       display_name=excluded.display_name,
                       default_language=excluded.default_language,
                       default_voice=excluded.default_voice,
                       extra_config=excluded.extra_config,
                       enabled=excluded.enabled,
                       updated_at=excluded.updated_at""",
                (
                    model_id,
                    modality,
                    provider_id,
                    model_name,
                    display_name,
                    default_language,
                    default_voice,
                    json.dumps(extra_config or {}),
                    1 if enabled else 0,
                    now,
                    now,
                ),
            )
            await db.commit()
        finally:
            await db.close()

    async def delete_managed_model(self, model_id: str) -> bool:
        db = await self._ensure_initialized()
        try:
            cursor = await db.execute(
                "DELETE FROM managed_models WHERE model_id = ?", (model_id,)
            )
            await db.commit()
            return (cursor.rowcount or 0) > 0
        finally:
            await db.close()

    # Managed projects

    @staticmethod
    def _validate_branding(branding: dict[str, Any] | None) -> dict[str, Any] | None:
        """Validate the branding payload before write."""
        import re

        if branding is None:
            return None
        if not isinstance(branding, dict):
            raise ValueError(
                f"branding must be a dict or None, got {type(branding).__name__}"
            )
        if not branding:
            return None
        out: dict[str, Any] = {}
        allowed = {"logo_url", "accent_color", "product_name"}
        for key in branding:
            if key not in allowed:
                raise ValueError(
                    f"branding has unknown key {key!r}; allowed: {sorted(allowed)}"
                )
        logo_url = branding.get("logo_url")
        if logo_url is not None:
            if not isinstance(logo_url, str) or len(logo_url) > 2048:
                raise ValueError("branding.logo_url must be a string up to 2048 chars")
            out["logo_url"] = logo_url
        accent_color = branding.get("accent_color")
        if accent_color is not None:
            if not isinstance(accent_color, str) or not re.fullmatch(
                r"#[0-9A-Fa-f]{3}([0-9A-Fa-f]{3})?", accent_color
            ):
                raise ValueError(
                    "branding.accent_color must be a hex string (#RGB or #RRGGBB)"
                )
            out["accent_color"] = accent_color
        product_name = branding.get("product_name")
        if product_name is not None:
            if not isinstance(product_name, str) or len(product_name) > 64:
                raise ValueError(
                    "branding.product_name must be a string up to 64 chars"
                )
            out["product_name"] = product_name
        return out or None

    async def list_managed_projects(self) -> list[dict[str, Any]]:
        db = await self._ensure_initialized()
        try:
            cursor = await db.execute(
                "SELECT project_id, name, description, daily_budget, budget_action, "
                "default_stack, stt_model, llm_model, tts_model, tags, "
                "created_at, updated_at, branding_json, guardrail_policy_json "
                "FROM managed_projects ORDER BY created_at ASC"
            )
            rows = []
            async for row in cursor:
                branding_raw = row[12] if len(row) > 12 else None
                guardrail_raw = row[13] if len(row) > 13 else None
                branding = None
                if branding_raw:
                    try:
                        branding = json.loads(branding_raw)
                    except (ValueError, TypeError):
                        branding = None
                guardrail_policy = None
                if guardrail_raw:
                    try:
                        guardrail_policy = json.loads(guardrail_raw)
                    except (ValueError, TypeError):
                        guardrail_policy = None
                rows.append(
                    {
                        "project_id": row[0],
                        "name": row[1],
                        "description": row[2],
                        "daily_budget": row[3],
                        "budget_action": row[4],
                        "default_stack": row[5],
                        "stt_model": row[6],
                        "llm_model": row[7],
                        "tts_model": row[8],
                        "tags": json.loads(row[9] or "[]"),
                        "created_at": row[10],
                        "updated_at": row[11],
                        "branding": branding,
                        "guardrail_policy": guardrail_policy,
                    }
                )
            return rows
        finally:
            await db.close()

    async def get_managed_project(self, project_id: str) -> dict[str, Any] | None:
        for p in await self.list_managed_projects():
            if p["project_id"] == project_id:
                return p
        return None

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
        validated_branding = self._validate_branding(branding)
        branding_json = json.dumps(validated_branding) if validated_branding else None
        validated_guardrails = (
            GuardrailPolicy.from_raw(guardrail_policy).to_storage_dict()
            if guardrail_policy is not None
            else None
        )
        guardrail_json = (
            json.dumps(validated_guardrails, sort_keys=True)
            if validated_guardrails is not None
            else None
        )
        db = await self._ensure_initialized()
        try:
            now = time.time()
            await db.execute(
                """INSERT INTO managed_projects
                       (project_id, name, description, daily_budget, budget_action,
                        default_stack, stt_model, llm_model, tts_model, tags,
                        created_at, updated_at, branding_json, guardrail_policy_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(project_id) DO UPDATE SET
                       name=excluded.name,
                       description=excluded.description,
                       daily_budget=excluded.daily_budget,
                       budget_action=excluded.budget_action,
                       default_stack=excluded.default_stack,
                       stt_model=excluded.stt_model,
                       llm_model=excluded.llm_model,
                       tts_model=excluded.tts_model,
                       tags=excluded.tags,
                       branding_json=COALESCE(excluded.branding_json, branding_json),
                       guardrail_policy_json=COALESCE(excluded.guardrail_policy_json, guardrail_policy_json),
                       updated_at=excluded.updated_at""",
                (
                    project_id,
                    name,
                    description,
                    daily_budget,
                    budget_action,
                    default_stack,
                    stt_model,
                    llm_model,
                    tts_model,
                    json.dumps(tags or []),
                    now,
                    now,
                    branding_json,
                    guardrail_json,
                ),
            )
            await db.commit()
        finally:
            await db.close()

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
        """Set or clear a project's guardrail policy overlay."""
        guardrail_json = None
        if policy is not None:
            validated = GuardrailPolicy.from_raw(policy).to_storage_dict()
            guardrail_json = json.dumps(validated, sort_keys=True)
        db = await self._ensure_initialized()
        try:
            now = time.time()
            await db.execute(
                """INSERT INTO managed_projects
                       (project_id, name, description, daily_budget, budget_action,
                        default_stack, stt_model, llm_model, tts_model, tags,
                        created_at, updated_at, guardrail_policy_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(project_id) DO UPDATE SET
                       guardrail_policy_json=excluded.guardrail_policy_json,
                       updated_at=excluded.updated_at""",
                (
                    project_id,
                    name,
                    description,
                    daily_budget,
                    budget_action,
                    default_stack,
                    stt_model,
                    llm_model,
                    tts_model,
                    json.dumps(tags or []),
                    now,
                    now,
                    guardrail_json,
                ),
            )
            await db.commit()
        finally:
            await db.close()

    async def delete_managed_project(self, project_id: str) -> bool:
        db = await self._ensure_initialized()
        try:
            cursor = await db.execute(
                "DELETE FROM managed_projects WHERE project_id = ?", (project_id,)
            )
            await db.commit()
            return (cursor.rowcount or 0) > 0
        finally:
            await db.close()
