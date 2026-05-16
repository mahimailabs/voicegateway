"""Async repo for the managed_models table (ORM)."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import delete, select

from voicegateway.models.managed_model_model import ManagedModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _row_to_dict(m: ManagedModel) -> dict[str, Any]:
    return {
        "model_id": m.model_id,
        "modality": m.modality,
        "provider_id": m.provider_id,
        "model_name": m.model_name,
        "display_name": m.display_name,
        "default_language": m.default_language,
        "default_voice": m.default_voice,
        "extra_config": json.loads(m.extra_config or "{}"),
        "enabled": bool(m.enabled),
        "created_at": m.created_at,
        "updated_at": m.updated_at,
    }


async def list_models(session: AsyncSession) -> list[dict[str, Any]]:
    """Return every managed_models row, ordered by created_at ASC."""
    result = await session.execute(
        select(ManagedModel).order_by(ManagedModel.created_at.asc())  # type: ignore[attr-defined]
    )
    return [_row_to_dict(m) for m in result.scalars().all()]


async def get_model(session: AsyncSession, model_id: str) -> dict[str, Any] | None:
    """Find one managed model by id."""
    m = await session.get(ManagedModel, model_id)
    return _row_to_dict(m) if m is not None else None


async def upsert_model(
    session: AsyncSession,
    model_id: str,
    modality: str,
    provider_id: str,
    model_name: str,
    display_name: str | None = None,
    default_language: str | None = None,
    default_voice: str | None = None,
    extra_config: dict[str, Any] | None = None,
    enabled: bool = True,
) -> None:
    """Insert or update one managed_models row."""
    now = time.time()
    values = {
        "model_id": model_id,
        "modality": modality,
        "provider_id": provider_id,
        "model_name": model_name,
        "display_name": display_name,
        "default_language": default_language,
        "default_voice": default_voice,
        "extra_config": json.dumps(extra_config or {}),
        "enabled": 1 if enabled else 0,
        "created_at": now,
        "updated_at": now,
    }
    stmt = sqlite_insert(ManagedModel.__table__).values(**values)  # type: ignore[attr-defined]
    stmt = stmt.on_conflict_do_update(
        index_elements=["model_id"],
        set_={
            "modality": stmt.excluded.modality,
            "provider_id": stmt.excluded.provider_id,
            "model_name": stmt.excluded.model_name,
            "display_name": stmt.excluded.display_name,
            "default_language": stmt.excluded.default_language,
            "default_voice": stmt.excluded.default_voice,
            "extra_config": stmt.excluded.extra_config,
            "enabled": stmt.excluded.enabled,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    await session.execute(stmt)
    await session.commit()


async def delete_model(session: AsyncSession, model_id: str) -> bool:
    """Delete one managed_models row. True when a row was removed."""
    result = await session.execute(
        delete(ManagedModel).where(ManagedModel.model_id == model_id)  # type: ignore[arg-type]
    )
    await session.commit()
    return (result.rowcount or 0) > 0  # type: ignore[attr-defined]


__all__ = ["delete_model", "get_model", "list_models", "upsert_model"]
