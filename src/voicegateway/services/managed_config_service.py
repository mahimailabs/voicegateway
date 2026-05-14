"""Umbrella service over the three managed_* repositories."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from voicegateway.repository import (
    managed_model_repository as model_repo,
)
from voicegateway.repository import (
    managed_project_repository as project_repo,
)
from voicegateway.repository import (
    managed_provider_repository as provider_repo,
)

if TYPE_CHECKING:
    from voicegateway.storage.connection import ConnectionManager


class ManagedConfigService:
    """CRUD over managed_providers / managed_models / managed_projects."""

    def __init__(self, connection_manager: ConnectionManager) -> None:
        self._conn = connection_manager

    # --- providers ---------------------------------------------------

    async def list_providers(self) -> list[dict[str, Any]]:
        db = await self._conn.connect()
        try:
            return await provider_repo.list_providers(db)
        finally:
            await db.close()

    async def get_provider(self, provider_id: str) -> dict[str, Any] | None:
        db = await self._conn.connect()
        try:
            return await provider_repo.get_provider(db, provider_id)
        finally:
            await db.close()

    async def upsert_provider(
        self,
        provider_id: str,
        provider_type: str,
        api_key: str,
        base_url: str | None = None,
        extra_config: dict[str, Any] | None = None,
        project: str | None = None,
    ) -> None:
        db = await self._conn.connect()
        try:
            await provider_repo.upsert_provider(
                db,
                provider_id,
                provider_type,
                api_key,
                base_url=base_url,
                extra_config=extra_config,
                project=project,
            )
        finally:
            await db.close()

    async def delete_provider(self, provider_id: str) -> bool:
        db = await self._conn.connect()
        try:
            return await provider_repo.delete_provider(db, provider_id)
        finally:
            await db.close()

    async def rotate_credentials(
        self, *, time_now: float | None = None
    ) -> dict[str, Any]:
        db = await self._conn.connect()
        try:
            return await provider_repo.rotate_credentials(db, time_now=time_now)
        finally:
            await db.close()

    # --- models ------------------------------------------------------

    async def list_models(self) -> list[dict[str, Any]]:
        async with self._conn.session() as s:
            return await model_repo.list_models(s)

    async def get_model(self, model_id: str) -> dict[str, Any] | None:
        async with self._conn.session() as s:
            return await model_repo.get_model(s, model_id)

    async def upsert_model(
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
        async with self._conn.session() as s:
            await model_repo.upsert_model(
                s,
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

    async def delete_model(self, model_id: str) -> bool:
        async with self._conn.session() as s:
            return await model_repo.delete_model(s, model_id)

    # --- projects ----------------------------------------------------

    async def list_projects(self) -> list[dict[str, Any]]:
        db = await self._conn.connect()
        try:
            return await project_repo.list_projects(db)
        finally:
            await db.close()

    async def get_project(self, project_id: str) -> dict[str, Any] | None:
        db = await self._conn.connect()
        try:
            return await project_repo.get_project(db, project_id)
        finally:
            await db.close()

    async def upsert_project(
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
        db = await self._conn.connect()
        try:
            await project_repo.upsert_project(
                db,
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
        finally:
            await db.close()

    async def set_project_guardrails(
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
        db = await self._conn.connect()
        try:
            await project_repo.set_project_guardrails(
                db,
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
        finally:
            await db.close()

    async def delete_project(self, project_id: str) -> bool:
        db = await self._conn.connect()
        try:
            return await project_repo.delete_project(db, project_id)
        finally:
            await db.close()


__all__ = ["ManagedConfigService"]
