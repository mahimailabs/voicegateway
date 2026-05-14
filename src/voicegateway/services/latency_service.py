"""Service wrapping the latency-aggregation repo."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from voicegateway.repository import latency_repository as repo

if TYPE_CHECKING:
    from voicegateway.storage.connection import ConnectionManager


class LatencyService:
    """Latency aggregation queries against the requests table."""

    def __init__(self, connection_manager: ConnectionManager) -> None:
        self._conn = connection_manager

    async def get_stats(
        self,
        period: str = "today",
        project: str | None = None,
        percentiles: list[float] | None = None,
        tenant: str | None = None,
    ) -> dict[str, Any]:
        """Per-model latency rollup with percentiles."""
        db = await self._conn.connect()
        try:
            return await repo.get_latency_stats(
                db,
                period=period,
                project=project,
                percentiles=percentiles,
                tenant=tenant,
            )
        finally:
            await db.close()

    async def get_samples(
        self,
        period: str = "today",
        project: str | None = None,
        modality: str | None = None,
    ) -> tuple[list[float], list[float]]:
        """Return ``(ttfb_samples, total_latency_samples)``."""
        db = await self._conn.connect()
        try:
            return await repo.get_latency_samples(
                db, period=period, project=project, modality=modality
            )
        finally:
            await db.close()


__all__ = ["LatencyService"]
