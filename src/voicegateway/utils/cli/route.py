"""Helpers for ``voicegateway.cli.route``."""

from __future__ import annotations

from typing import Any

from voicegateway.middleware import router
from voicegateway.repository import (
    latency_observations_repository as latency_observations,
)


async def _show_async(storage: Any, project_id: str) -> list[Any]:
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        return await latency_observations.get_for_project(db, project_id)


async def _simulate_async(
    storage: Any,
    *,
    project_id: str,
    project_config: Any,
    overrides: dict[str, str],
) -> Any:
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        return await router.route_session(
            db,
            project_id=project_id,
            project_config=project_config,
            caller_overrides=overrides or None,
        )
