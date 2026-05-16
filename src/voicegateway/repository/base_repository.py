"""Generic async repository: shared CRUD over any SQLModel entity."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Generic, TypeVar

from sqlalchemy import exc as sa_exc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel, func, select

from voicegateway.core.exceptions import DuplicatedError, NotFoundError

T = TypeVar("T", bound=SQLModel)

DEFAULT_PAGE: int = 1
DEFAULT_PAGE_SIZE: int = 20

SessionFactory = Callable[..., AbstractAsyncContextManager[AsyncSession]]


class BaseRepository(Generic[T]):
    """Async CRUD over a single :class:`SQLModel` table."""

    def __init__(self, session_factory: SessionFactory, model: type[T]) -> None:
        self.session_factory = session_factory
        self.model = model

    @asynccontextmanager
    async def _session(
        self, external: AsyncSession | None
    ) -> AsyncIterator[AsyncSession]:
        """Reuse an injected session or open a new one."""
        if external is not None:
            yield external
            return
        async with self.session_factory() as s:
            yield s

    async def read_by_id(
        self, entity_id: int | str, *, session: AsyncSession | None = None
    ) -> T:
        """Return the row keyed by primary key. Raises :class:`NotFoundError`."""
        async with self._session(session) as s:
            obj = await s.get(self.model, entity_id)
            if obj is None:
                raise NotFoundError(
                    detail=f"{self.model.__name__} {entity_id} not found"
                )
            return obj

    async def list_paginated(
        self,
        *,
        page: int = DEFAULT_PAGE,
        page_size: int = DEFAULT_PAGE_SIZE,
        session: AsyncSession | None = None,
    ) -> tuple[list[T], int]:
        """Return ``(rows, total_count)`` for the requested page."""
        offset = max(0, (page - 1) * page_size)
        async with self._session(session) as s:
            rows_exec = await s.execute(
                select(self.model).limit(page_size).offset(offset)
            )
            rows: list[T] = list(rows_exec.scalars().all())
            count_exec = await s.execute(select(func.count()).select_from(self.model))
            total = int(count_exec.scalar() or 0)
            return rows, total

    async def create(
        self, payload: T | dict[str, Any], *, session: AsyncSession | None = None
    ) -> T:
        """Insert a row. Unique-violations surface as :class:`DuplicatedError`."""
        obj = payload if isinstance(payload, SQLModel) else self.model(**payload)
        async with self._session(session) as s:
            try:
                s.add(obj)
                if session is None:
                    await s.commit()
                    await s.refresh(obj)
                else:
                    await s.flush()
                    await s.refresh(obj)
                return obj
            except sa_exc.IntegrityError as e:
                if session is None:
                    await s.rollback()
                raise DuplicatedError(detail=str(e.orig)) from e

    async def update(
        self,
        entity_id: int | str,
        patch: dict[str, Any],
        *,
        session: AsyncSession | None = None,
    ) -> T:
        """Partial update. Skips ``None`` values to keep PATCH semantics."""
        async with self._session(session) as s:
            obj = await s.get(self.model, entity_id)
            if obj is None:
                raise NotFoundError(
                    detail=f"{self.model.__name__} {entity_id} not found"
                )
            for key, value in patch.items():
                if value is not None:
                    setattr(obj, key, value)
            s.add(obj)
            if session is None:
                await s.commit()
                await s.refresh(obj)
            else:
                await s.flush()
                await s.refresh(obj)
            return obj

    async def delete_by_id(
        self, entity_id: int | str, *, session: AsyncSession | None = None
    ) -> None:
        """Hard-delete. Raises :class:`NotFoundError` when the row is missing."""
        async with self._session(session) as s:
            obj = await s.get(self.model, entity_id)
            if obj is None:
                raise NotFoundError(
                    detail=f"{self.model.__name__} {entity_id} not found"
                )
            await s.delete(obj)
            if session is None:
                await s.commit()
            else:
                await s.flush()
