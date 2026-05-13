"""Thin service-layer delegate over a single repository."""

from __future__ import annotations

from typing import Any, Generic, Protocol, TypeVar

T = TypeVar("T")


class _CrudRepository(Protocol[T]):
    """Subset of :class:`voicegateway.repository.base.BaseRepository` used here."""

    async def read_by_id(self, entity_id: int | str) -> T: ...
    async def list_paginated(
        self, *, page: int = ..., page_size: int = ...
    ) -> tuple[list[T], int]: ...
    async def create(self, payload: Any) -> T: ...
    async def update(self, entity_id: int | str, patch: dict[str, Any]) -> T: ...
    async def delete_by_id(self, entity_id: int | str) -> None: ...


class BaseService(Generic[T]):
    """Pass-through delegate. Domain services override what needs policy."""

    def __init__(self, repository: _CrudRepository[T]) -> None:
        self._repository = repository

    async def get_by_id(self, entity_id: int | str) -> T:
        return await self._repository.read_by_id(entity_id)

    async def list_paginated(
        self, *, page: int = 1, page_size: int = 20
    ) -> tuple[list[T], int]:
        return await self._repository.list_paginated(page=page, page_size=page_size)

    async def add(self, payload: Any) -> T:
        return await self._repository.create(payload)

    async def patch(self, entity_id: int | str, patch: dict[str, Any]) -> T:
        return await self._repository.update(entity_id, patch)

    async def remove_by_id(self, entity_id: int | str) -> None:
        await self._repository.delete_by_id(entity_id)
