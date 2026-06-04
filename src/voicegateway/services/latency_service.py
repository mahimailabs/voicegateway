"""Service wrapping the latency-aggregation repo."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from voicegateway.repository import latency_repository as repo

if TYPE_CHECKING:
    from voicegateway.core.database import Database


class LatencyService:
    """Latency aggregation queries against the requests table."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def get_stats(
        self,
        period: str = "today",
        project: str | None = None,
        percentiles: list[float] | None = None,
        tenant: str | None = None,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """Per-model latency rollup with percentiles."""
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
