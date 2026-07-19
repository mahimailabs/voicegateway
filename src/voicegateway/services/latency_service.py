"""Service wrapping the latency-aggregation repo.

When ``cost_tracking.read_engine == "duckdb"`` (SQLite backend, duckdb
installed), ``get_stats`` computes percentiles in-DB via
:mod:`voicegateway.analytics.duckdb_reader` instead of pulling every raw sample
into Python, with a fallback to the SQLite path on any error.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from voicegateway.analytics import duckdb_reader
from voicegateway.repository import latency_repository as repo

if TYPE_CHECKING:
    from voicegateway.core.database import Database

logger = logging.getLogger(__name__)


class LatencyService:
    """Latency aggregation queries against the requests table."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def _use_duckdb(self) -> bool:
        ct = self._db.config.cost_tracking or {}
        return (
            ct.get("read_engine") == "duckdb"
            and self._db.is_sqlite
            and duckdb_reader.available()
        )

    async def get_stats(
        self,
        period: str = "today",
        project: str | None = None,
        percentiles: list[float] | None = None,
        tenant: str | None = None,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """Per-model latency rollup with percentiles."""
        if self._use_duckdb():
            try:
                return await asyncio.to_thread(
                    duckdb_reader.latency_stats,
                    self._db.db_file_path,
                    period=period,
                    project=project,
                    percentiles=percentiles,
                    tenant=tenant,
                    agent=agent,
                )
            except Exception:
                logger.warning(
                    "duckdb latency stats failed; using sqlite", exc_info=True
                )
        async with self._db.session() as s:
            return await repo.get_latency_stats(
                s,
                period=period,
                project=project,
                percentiles=percentiles,
                tenant=tenant,
                agent=agent,
            )

    async def get_samples(
        self,
        period: str = "today",
        project: str | None = None,
        modality: str | None = None,
    ) -> tuple[list[float], list[float]]:
        """Return ``(ttfb_samples, total_latency_samples)``."""
        async with self._db.session() as s:
            return await repo.get_latency_samples(
                s, period=period, project=project, modality=modality
            )


__all__ = ["LatencyService"]
