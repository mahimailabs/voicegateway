"""Service wrapping the request_log repository with connection lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from voicegateway.repository import request_log_repository as repo

if TYPE_CHECKING:
    from voicegateway.models.request_model import RequestRecord
    from voicegateway.storage.connection import ConnectionManager


class RequestLogService:
    """Orchestrates connection lifecycle around the request_log repo."""

    def __init__(self, connection_manager: ConnectionManager) -> None:
        self._conn = connection_manager

    async def log_request(self, record: RequestRecord) -> None:
        """Persist one request row + accumulate the session row."""
        db = await self._conn.connect()
        try:
            await repo.log_request(db, record)
        finally:
            await db.close()

    async def log_audit_event(
        self,
        entity_type: str,
        entity_id: str,
        action: str,
        changes: dict[str, Any] | None = None,
        source: str = "api",
    ) -> None:
        """Append one config_audit_log row."""
        db = await self._conn.connect()
        try:
            await repo.log_audit_event(
                db, entity_type, entity_id, action, changes, source
            )
        finally:
            await db.close()

    async def get_audit_log(
        self,
        limit: int = 50,
        entity_type: str | None = None,
        entity_id: str | None = None,
        action: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return recent audit log entries."""
        db = await self._conn.connect()
        try:
            return await repo.get_audit_log(db, limit, entity_type, entity_id, action)
        finally:
            await db.close()

    async def get_recent_requests(
        self,
        limit: int = 100,
        modality: str | None = None,
        project: str | None = None,
        tenant: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the N newest request rows."""
        db = await self._conn.connect()
        try:
            return await repo.get_recent_requests(db, limit, modality, project, tenant)
        finally:
            await db.close()

    async def get_requests_in_window(
        self,
        start_ts: float | None = None,
        end_ts: float | None = None,
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return every request row falling in ``[start_ts, end_ts)``."""
        db = await self._conn.connect()
        try:
            return await repo.get_requests_in_window(db, start_ts, end_ts, project)
        finally:
            await db.close()


__all__ = ["RequestLogService"]
