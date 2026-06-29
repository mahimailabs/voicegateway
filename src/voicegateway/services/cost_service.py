"""Service wrapping the cost-aggregation repo with session lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from voicegateway.repository import cost_repository as repo

if TYPE_CHECKING:
    from voicegateway.core.database import Database


class CostService:
    """Cost aggregation queries against the requests table."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def get_summary(
        self,
        period: str = "today",
        project: str | None = None,
        include_pricing_source: bool = False,
        start_ts: float | None = None,
        end_ts: float | None = None,
        tenant: str | None = None,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """Return total / by_provider / by_model rollups."""
        async with self._db.session() as s:
            return await repo.get_cost_summary(
                s,
                period=period,
                project=project,
                include_pricing_source=include_pricing_source,
                start_ts=start_ts,
                end_ts=end_ts,
                tenant=tenant,
                agent=agent,
            )

    async def get_by_project(
        self,
        period: str = "today",
        start_ts: float | None = None,
        end_ts: float | None = None,
        tenant: str | None = None,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """Return cost rollup grouped by project."""
        async with self._db.session() as s:
            return await repo.get_cost_by_project(
                s,
                period=period,
                start_ts=start_ts,
                end_ts=end_ts,
                tenant=tenant,
                agent=agent,
            )

    async def get_by_day(
        self,
        period: str = "week",
        project: str | None = None,
        start_ts: float | None = None,
        end_ts: float | None = None,
        tenant: str | None = None,
        agent: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return a day-bucketed cost series, ascending by day."""
        async with self._db.session() as s:
            return await repo.get_cost_by_day(
                s,
                period=period,
                project=project,
                start_ts=start_ts,
                end_ts=end_ts,
                tenant=tenant,
                agent=agent,
            )

    async def get_by_modality(
        self,
        period: str = "today",
        project: str | None = None,
        start_ts: float | None = None,
        end_ts: float | None = None,
    ) -> dict[str, Any]:
        """Return cost rollup grouped by modality."""
        async with self._db.session() as s:
            return await repo.get_cost_by_modality(
                s,
                period=period,
                project=project,
                start_ts=start_ts,
                end_ts=end_ts,
            )

    async def get_project_stats(self, project: str) -> dict[str, Any]:
        """Per-project today snapshot."""
        async with self._db.session() as s:
            return await repo.get_project_stats(s, project)


__all__ = ["CostService"]
