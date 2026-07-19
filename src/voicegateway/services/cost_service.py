"""Service wrapping the cost-aggregation repo with session lifecycle.

When ``cost_tracking.read_engine == "duckdb"`` (and the backend is SQLite and
duckdb is installed), the aggregation reads run through
:mod:`voicegateway.analytics.duckdb_reader` (columnar-fast, in-DB), with an
automatic fallback to the SQLite ORM path on any error. Results are identical;
only the read speed changes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from voicegateway.analytics import duckdb_reader
from voicegateway.repository import cost_repository as repo

if TYPE_CHECKING:
    from voicegateway.core.database import Database

logger = logging.getLogger(__name__)


class CostService:
    """Cost aggregation queries against the requests table."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def _use_duckdb(self) -> bool:
        ct = self._db.config.cost_tracking or {}
        return (
            ct.get("read_engine") == "duckdb"
            and self._db.is_sqlite
            and duckdb_reader.available()
        )

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
        if self._use_duckdb():
            try:
                return await asyncio.to_thread(
                    duckdb_reader.cost_summary,
                    self._db.db_file_path,
                    period=period,
                    project=project,
                    include_pricing_source=include_pricing_source,
                    start_ts=start_ts,
                    end_ts=end_ts,
                    tenant=tenant,
                    agent=agent,
                )
            except Exception:
                logger.warning(
                    "duckdb cost summary failed; using sqlite", exc_info=True
                )
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
        if self._use_duckdb():
            try:
                return await asyncio.to_thread(
                    duckdb_reader.cost_by_project,
                    self._db.db_file_path,
                    period=period,
                    start_ts=start_ts,
                    end_ts=end_ts,
                    tenant=tenant,
                    agent=agent,
                )
            except Exception:
                logger.warning(
                    "duckdb cost by project failed; using sqlite", exc_info=True
                )
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
        if self._use_duckdb():
            try:
                return await asyncio.to_thread(
                    duckdb_reader.cost_by_day,
                    self._db.db_file_path,
                    period=period,
                    project=project,
                    start_ts=start_ts,
                    end_ts=end_ts,
                    tenant=tenant,
                    agent=agent,
                )
            except Exception:
                logger.warning("duckdb cost by day failed; using sqlite", exc_info=True)
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
        if self._use_duckdb():
            try:
                return await asyncio.to_thread(
                    duckdb_reader.cost_by_modality,
                    self._db.db_file_path,
                    period=period,
                    project=project,
                    start_ts=start_ts,
                    end_ts=end_ts,
                )
            except Exception:
                logger.warning(
                    "duckdb cost by modality failed; using sqlite", exc_info=True
                )
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
