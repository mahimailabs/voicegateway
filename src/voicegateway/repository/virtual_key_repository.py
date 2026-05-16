"""ORM-based repository for :class:`voicegateway.models.virtual_key_model.VirtualKey`."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import func, select

from voicegateway.models.virtual_key_model import VirtualKey
from voicegateway.repository.base_repository import BaseRepository, SessionFactory


class VirtualKeyRepository(BaseRepository[VirtualKey]):
    """Async CRUD + domain queries over ``virtual_keys``."""

    def __init__(self, session_factory: SessionFactory) -> None:
        super().__init__(session_factory, VirtualKey)

    async def find_by_prefix(
        self, prefix: str, *, session: AsyncSession | None = None
    ) -> list[VirtualKey]:
        """Return every row matching the visible 8-char prefix."""
        async with self._session(session) as s:
            result = await s.execute(
                select(VirtualKey).where(VirtualKey.key_prefix == prefix)
            )
            return list(result.scalars().all())

    async def list_keys(
        self,
        *,
        include_revoked: bool = True,
        session: AsyncSession | None = None,
    ) -> list[VirtualKey]:
        """Return all keys, newest first. Filters out revoked when asked."""
        async with self._session(session) as s:
            stmt = select(VirtualKey)
            if not include_revoked:
                stmt = stmt.where(VirtualKey.revoked_at.is_(None))  # type: ignore[union-attr]
            stmt = stmt.order_by(VirtualKey.issued_at.desc(), VirtualKey.id.desc())  # type: ignore[union-attr,attr-defined]
            result = await s.execute(stmt)
            return list(result.scalars().all())

    async def mark_used(
        self, key_id: int, *, session: AsyncSession | None = None
    ) -> None:
        """Bump ``last_used_at`` to now. Idempotent."""
        async with self._session(session) as s:
            obj = await s.get(VirtualKey, key_id)
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
            obj = await s.get(VirtualKey, key_id)
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
    ) -> list[VirtualKey]:
        """Return non-revoked keys whose last-used (or issued) is older than the cutoff."""
        if stale_after_days < 0:
            raise ValueError(f"stale_after_days must be >= 0, got {stale_after_days}")
        cutoff_clause = func.coalesce(VirtualKey.last_used_at, VirtualKey.issued_at)
        async with self._session(session) as s:
            stmt = (
                select(VirtualKey)
                .where(VirtualKey.revoked_at.is_(None))  # type: ignore[union-attr]
                .where(
                    cutoff_clause <= func.datetime("now", f"-{stale_after_days} days")
                )
                .order_by(cutoff_clause.asc(), VirtualKey.id.asc())  # type: ignore[union-attr]
            )
            result = await s.execute(stmt)
            return list(result.scalars().all())
