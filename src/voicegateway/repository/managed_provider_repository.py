"""Async repo for the managed_providers table."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiosqlite


async def list_providers(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    """Return managed providers. api_key_encrypted is ciphertext."""
    cursor = await db.execute(
        "SELECT provider_id, provider_type, api_key_encrypted, base_url, "
        "extra_config, created_at, updated_at, project FROM managed_providers "
        "ORDER BY created_at ASC"
    )
    rows: list[dict[str, Any]] = []
    async for row in cursor:
        rows.append(_row_to_dict(row))
    return rows


async def get_provider(
    db: aiosqlite.Connection, provider_id: str
) -> dict[str, Any] | None:
    """Return a managed provider by id, or None if missing."""
    cursor = await db.execute(
        "SELECT provider_id, provider_type, api_key_encrypted, base_url, "
        "extra_config, created_at, updated_at, project FROM managed_providers "
        "WHERE provider_id = ?",
        (provider_id,),
    )
    row = await cursor.fetchone()
    return _row_to_dict(row) if row is not None else None


async def upsert_provider(
    db: aiosqlite.Connection,
    provider_id: str,
    provider_type: str,
    api_key: str,
    base_url: str | None = None,
    extra_config: dict[str, Any] | None = None,
    project: str | None = None,
) -> None:
    """Insert or update a managed provider row. Encrypts the api_key."""
    from voicegateway.core.crypto import encrypt

    now = time.time()
    encrypted_key = encrypt(api_key)
    await db.execute(
        """INSERT INTO managed_providers
               (provider_id, provider_type, api_key_encrypted, base_url,
                extra_config, created_at, updated_at, project)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(provider_id) DO UPDATE SET
               provider_type=excluded.provider_type,
               api_key_encrypted=excluded.api_key_encrypted,
               base_url=excluded.base_url,
               extra_config=excluded.extra_config,
               updated_at=excluded.updated_at,
               project=excluded.project""",
        (
            provider_id,
            provider_type,
            encrypted_key,
            base_url,
            json.dumps(extra_config or {}),
            now,
            now,
            project,
        ),
    )
    await db.commit()


async def delete_provider(db: aiosqlite.Connection, provider_id: str) -> bool:
    """Delete one managed provider row. Returns True when a row was removed."""
    cursor = await db.execute(
        "DELETE FROM managed_providers WHERE provider_id = ?", (provider_id,)
    )
    await db.commit()
    return (cursor.rowcount or 0) > 0


async def rotate_credentials(
    db: aiosqlite.Connection, *, time_now: float | None = None
) -> dict[str, Any]:
    """Re-encrypt every managed_providers row under the current Fernet key."""
    from voicegateway.core.crypto import rotate_token

    now = time_now if time_now is not None else time.time()
    rotated = 0
    skipped_empty = 0
    failed: list[str] = []

    cursor = await db.execute(
        "SELECT provider_id, api_key_encrypted FROM managed_providers"
    )
    rows = await cursor.fetchall()
    for provider_id, ciphertext in rows:
        if not ciphertext:
            skipped_empty += 1
            continue
        try:
            new_ciphertext = rotate_token(ciphertext)
        except ValueError:
            failed.append(provider_id)
            continue
        await db.execute(
            "UPDATE managed_providers SET api_key_encrypted = ?, "
            "updated_at = ? WHERE provider_id = ?",
            (new_ciphertext, now, provider_id),
        )
        rotated += 1
    await db.commit()
    return {"rotated": rotated, "skipped_empty": skipped_empty, "failed": failed}


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "provider_id": row[0],
        "provider_type": row[1],
        "api_key_encrypted": row[2],
        "base_url": row[3],
        "extra_config": json.loads(row[4] or "{}"),
        "created_at": row[5],
        "updated_at": row[6],
        "project": row[7],
    }


__all__ = [
    "delete_provider",
    "get_provider",
    "list_providers",
    "rotate_credentials",
    "upsert_provider",
]
