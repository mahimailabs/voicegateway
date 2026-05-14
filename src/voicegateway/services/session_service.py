"""Service wrapping the session repo with session lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from voicegateway.repository import session_repository as repo

if TYPE_CHECKING:
    from voicegateway.storage.connection import ConnectionManager


class SessionService:
    """Sessions table queries + finalize-aggregates writes."""

    def __init__(self, connection_manager: ConnectionManager) -> None:
        self._conn = connection_manager

    async def list_sessions(
        self,
        limit: int = 100,
        project: str | None = None,
        order_by: str = "started_at_desc",
        tenant: str | None = None,
    ) -> list[dict[str, Any]]:
        async with self._conn.session() as s:
            return await repo.list_sessions(
                s, limit=limit, project=project, order_by=order_by, tenant=tenant
            )

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        async with self._conn.session() as s:
            return await repo.get_session(s, session_id)

    async def finalize_metrics(self, session_id: str) -> None:
        async with self._conn.session() as s:
            await repo.finalize_session_metrics(s, session_id)

    async def finalize_replay(self, session_id: str) -> None:
        async with self._conn.session() as s:
            await repo.finalize_session_replay(s, session_id)


__all__ = ["SessionService"]
