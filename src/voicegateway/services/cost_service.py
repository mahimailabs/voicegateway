"""Service wrapping the cost-aggregation repo with connection lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from voicegateway.repository import cost_repository as repo

if TYPE_CHECKING:
    from voicegateway.storage.connection import ConnectionManager


class CostService:
    """Cost aggregation queries against the requests table."""

    def __init__(self, connection_manager: ConnectionManager) -> None:
        self._conn = connection_manager

    async def get_summary(
        self,
        period: str = "today",
        project: str | None = None,
        include_pricing_source: bool = False,
        start_ts: float | None = None,
        end_ts: float | None = None,
        tenant: str | None = None,
    ) -> dict[str, Any]:
        """Return total / by_provider / by_model rollups."""
        db = await self._conn.connect()
        try:
            return await repo.get_cost_summary(
                db,
                period=period,
                project=project,
                include_pricing_source=include_pricing_source,
                start_ts=start_ts,
                end_ts=end_ts,
                tenant=tenant,
            )
        finally:
            await db.close()

    async def get_by_project(
        self,
        period: str = "today",
        start_ts: float | None = None,
        end_ts: float | None = None,
        tenant: str | None = None,
    ) -> dict[str, Any]:
        """Return cost rollup grouped by project."""
        db = await self._conn.connect()
        try:
            return await repo.get_cost_by_project(
                db, period=period, start_ts=start_ts, end_ts=end_ts, tenant=tenant
            )
        finally:
            await db.close()

    async def get_by_modality(
        self,
        period: str = "today",
        project: str | None = None,
        start_ts: float | None = None,
        end_ts: float | None = None,
    ) -> dict[str, Any]:
        """Return cost rollup grouped by modality."""
        db = await self._conn.connect()
        try:
            return await repo.get_cost_by_modality(
                db,
                period=period,
                project=project,
                start_ts=start_ts,
                end_ts=end_ts,
            )
        finally:
            await db.close()

    async def get_project_stats(self, project: str) -> dict[str, Any]:
        """Per-project today snapshot."""
        db = await self._conn.connect()
        try:
            return await repo.get_project_stats(db, project)
        finally:
            await db.close()


__all__ = ["CostService"]
