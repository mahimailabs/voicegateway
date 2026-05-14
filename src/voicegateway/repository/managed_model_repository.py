"""Async repo for the managed_models table."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiosqlite


async def list_models(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    """Return every managed_models row, ordered by created_at ASC."""
    cursor = await db.execute(
        "SELECT model_id, modality, provider_id, model_name, display_name, "
        "default_language, default_voice, extra_config, enabled, "
        "created_at, updated_at FROM managed_models ORDER BY created_at ASC"
    )
    rows: list[dict[str, Any]] = []
    async for row in cursor:
        rows.append(
            {
                "model_id": row[0],
                "modality": row[1],
                "provider_id": row[2],
                "model_name": row[3],
                "display_name": row[4],
                "default_language": row[5],
                "default_voice": row[6],
                "extra_config": json.loads(row[7] or "{}"),
                "enabled": bool(row[8]),
                "created_at": row[9],
                "updated_at": row[10],
            }
        )
    return rows


async def get_model(db: aiosqlite.Connection, model_id: str) -> dict[str, Any] | None:
    """Find one managed model by id (linear scan over the small table)."""
    for m in await list_models(db):
        if m["model_id"] == model_id:
            return m
    return None


async def upsert_model(
    db: aiosqlite.Connection,
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
    await db.execute(
        """INSERT INTO managed_models
               (model_id, modality, provider_id, model_name, display_name,
                default_language, default_voice, extra_config, enabled,
                created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(model_id) DO UPDATE SET
               modality=excluded.modality,
               provider_id=excluded.provider_id,
               model_name=excluded.model_name,
               display_name=excluded.display_name,
               default_language=excluded.default_language,
               default_voice=excluded.default_voice,
               extra_config=excluded.extra_config,
               enabled=excluded.enabled,
               updated_at=excluded.updated_at""",
        (
            model_id,
            modality,
            provider_id,
            model_name,
            display_name,
            default_language,
            default_voice,
            json.dumps(extra_config or {}),
            1 if enabled else 0,
            now,
            now,
        ),
    )
    await db.commit()


async def delete_model(db: aiosqlite.Connection, model_id: str) -> bool:
    """Delete one managed_models row. Returns True when a row was removed."""
    cursor = await db.execute(
        "DELETE FROM managed_models WHERE model_id = ?", (model_id,)
    )
    await db.commit()
    return (cursor.rowcount or 0) > 0


__all__ = ["delete_model", "get_model", "list_models", "upsert_model"]
