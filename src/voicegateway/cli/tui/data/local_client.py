"""SQLite-backed ``MetricsClient`` for Local mode."""

from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Any

from voicegateway.cli.tui.data.exceptions import LocalModeUnsupportedError
from voicegateway.services.storage_service import StorageService


class LocalClient:
    """SQLite-backed :class:`MetricsClient` for daemon-down access."""

    def __init__(
        self,
        *,
        db_path: str | Path,
        poll_seconds: float = 5.0,
        storage: StorageService | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self.poll_seconds = poll_seconds
        self._storage = (
            storage if storage is not None else StorageService(self._db_path)
        )

    # -- lifecycle ---------------------------------------------------

    async def aclose(self) -> None:
        """Close any raw SQLite handles still open from in-flight polls."""
        await self._storage.aclose()

    async def __aenter__(self) -> LocalClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    # -- MetricsClient methods --------------------------------------

    async def list_sessions(
        self,
        *,
        limit: int = 100,
        project: str | None = None,
        order_by: str = "started_at_desc",
    ) -> list[dict[str, Any]]:
        """Delegate to ``StorageService.list_sessions``."""
        return await self._storage.list_sessions(
            limit=limit, project=project, order_by=order_by
        )

    async def get_session_detail(self, session_id: str) -> dict[str, Any] | None:
        """Delegate to ``StorageService.get_session``."""
        return await self._storage.get_session(session_id)

    async def list_costs(
        self,
        *,
        period: str = "today",
        project: str | None = None,
        include_pricing_source: bool = False,
    ) -> dict[str, Any]:
        """Combine total + per-modality so the response shape matches"""
        summary = await self._storage.get_cost_summary(
            period=period,
            project=project,
            include_pricing_source=include_pricing_source,
        )
        by_modality = await self._storage.get_cost_by_modality(
            period=period, project=project
        )
        result: dict[str, Any] = dict(summary) if isinstance(summary, dict) else {}

        if isinstance(by_modality, dict) and "by_modality" in by_modality:
            result["by_modality"] = by_modality["by_modality"]
        else:
            result["by_modality"] = by_modality
        return result

    async def list_logs(
        self,
        *,
        limit: int = 100,
        project: str | None = None,
        modality: str | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate to ``StorageService.get_recent_requests``."""
        return await self._storage.get_recent_requests(
            limit=limit, modality=modality, project=project
        )

    async def list_providers(
        self,
        *,
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate to ``StorageService.list_managed_providers``."""
        rows = await self._storage.list_managed_providers()
        if project is None:
            return rows
        return [row for row in rows if row.get("project") == project]

    async def test_provider(self, provider_id: str) -> dict[str, Any]:
        """Always raises ``LocalModeUnsupportedError(feature='test_provider')``."""
        raise LocalModeUnsupportedError(feature="test_provider")


__all__ = ["LocalClient"]
