"""ORM-based repository for :class:`voicegateway.models.api_key_model.ApiKey`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import func, select

from voicegateway.models.api_key_model import ApiKey
from voicegateway.repository.base_repository import BaseRepository, SessionFactory


class ApiKeyRepository(BaseRepository[ApiKey]):
    """Async CRUD + domain queries over ``api_keys``."""

    def __init__(self, session_factory: SessionFactory) -> None:
        super().__init__(session_factory, ApiKey)

    async def find_by_prefix(
        self, prefix: str, *, session: AsyncSession | None = None
    ) -> list[ApiKey]:
        """Return every row matching the visible 8-char prefix."""
        async with self._session(session) as s:
            result = await s.execute(
                select(ApiKey).where(ApiKey.key_prefix == prefix)
            )
            return list(result.scalars().all())

    async def list_keys(
        self,
        *,
        include_revoked: bool = True,
        session: AsyncSession | None = None,
    ) -> list[ApiKey]:
        """Return all keys, newest first. Filters out revoked when asked."""
        async with self._session(session) as s:
            stmt = select(ApiKey)
            if not include_revoked:
                stmt = stmt.where(ApiKey.revoked_at.is_(None))  # type: ignore[union-attr]
            stmt = stmt.order_by(ApiKey.issued_at.desc(), ApiKey.id.desc())  # type: ignore[union-attr,attr-defined]
            result = await s.execute(stmt)
            return list(result.scalars().all())

    async def mark_used(
        self, key_id: int, *, session: AsyncSession | None = None
    ) -> None:
        """Bump ``last_used_at`` to now. Idempotent."""
        async with self._session(session) as s:
            obj = await s.get(ApiKey, key_id)
            if obj is None:
                return
            obj.last_used_at = datetime.now(UTC)
            s.add(obj)
            if session is None:
                await s.commit()
            else:
                await s.flush()

    async def revoke(self, key_id: int, *, session: AsyncSession | None = None) -> bool:
        """Soft-revoke. Returns True on the transition, False if already revoked."""
        async with self._session(session) as s:
            obj = await s.get(ApiKey, key_id)
            if obj is None or obj.revoked_at is not None:
                return False
            obj.revoked_at = datetime.now(UTC)
            s.add(obj)
            if session is None:
                await s.commit()
            else:
                await s.flush()
            return True

    async def list_stale(
        self,
        *,
        stale_after_days: int,
        session: AsyncSession | None = None,
    ) -> list[ApiKey]:
        """Return non-revoked keys whose last-used (or issued) is older than the cutoff."""
        if stale_after_days < 0:
            raise ValueError(f"stale_after_days must be >= 0, got {stale_after_days}")
        cutoff_clause = func.coalesce(ApiKey.last_used_at, ApiKey.issued_at)
        # Compute the cutoff in Python instead of the SQLite-only datetime()
        # function, so the comparison adapts to SQLite and Postgres alike.
        cutoff = datetime.now(UTC) - timedelta(days=stale_after_days)
        async with self._session(session) as s:
            stmt = (
                select(ApiKey)
                .where(ApiKey.revoked_at.is_(None))  # type: ignore[union-attr]
                .where(cutoff_clause <= cutoff)
                .order_by(cutoff_clause.asc(), ApiKey.id.asc())  # type: ignore[union-attr]
            )
            result = await s.execute(stmt)
            return list(result.scalars().all())
