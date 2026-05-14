"""Service wrapping the session repo with connection lifecycle."""

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
        """Return recent sessions."""
        db = await self._conn.connect()
        try:
            return await repo.list_sessions(
                db, limit=limit, project=project, order_by=order_by, tenant=tenant
            )
        finally:
            await db.close()

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Return one session with per-modality / provider / guardrail-event drill-in."""
        db = await self._conn.connect()
        try:
            return await repo.get_session(db, session_id)
        finally:
            await db.close()

    async def finalize_metrics(self, session_id: str) -> None:
        """Recompute the five session-aggregate columns from the turns table."""
        db = await self._conn.connect()
        try:
            await repo.finalize_session_metrics(db, session_id)
        finally:
            await db.close()

    async def finalize_replay(self, session_id: str) -> None:
        """Compute replay_size_bytes for the session and persist."""
        db = await self._conn.connect()
        try:
            await repo.finalize_session_replay(db, session_id)
        finally:
            await db.close()


__all__ = ["SessionService"]
