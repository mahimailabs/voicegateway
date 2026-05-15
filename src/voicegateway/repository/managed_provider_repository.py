"""Async repo for the managed_providers table (ORM)."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import delete, select

from voicegateway.core.crypto import encrypt, rotate_token
from voicegateway.models.managed_provider_model import ManagedProvider

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _row_to_dict(p: ManagedProvider) -> dict[str, Any]:
    return {
        "provider_id": p.provider_id,
        "provider_type": p.provider_type,
        "api_key_encrypted": p.api_key_encrypted,
        "base_url": p.base_url,
        "extra_config": json.loads(p.extra_config or "{}"),
        "created_at": p.created_at,
        "updated_at": p.updated_at,
        "project": p.project,
    }


async def list_providers(session: AsyncSession) -> list[dict[str, Any]]:
    """Return managed providers. api_key_encrypted is ciphertext."""
    result = await session.execute(
        select(ManagedProvider).order_by(ManagedProvider.created_at.asc())
    )
    return [_row_to_dict(p) for p in result.scalars().all()]


async def get_provider(
    session: AsyncSession, provider_id: str
) -> dict[str, Any] | None:
    """Return a managed provider by id, or None if missing."""
    p = await session.get(ManagedProvider, provider_id)
    return _row_to_dict(p) if p is not None else None


async def upsert_provider(
    session: AsyncSession,
    provider_id: str,
    provider_type: str,
    api_key: str,
    base_url: str | None = None,
    extra_config: dict[str, Any] | None = None,
    project: str | None = None,
) -> None:
    """Insert or update a managed provider row. Encrypts the api_key."""
    now = time.time()
    encrypted_key = encrypt(api_key)
    values = {
        "provider_id": provider_id,
        "provider_type": provider_type,
        "api_key_encrypted": encrypted_key,
        "base_url": base_url,
        "extra_config": json.dumps(extra_config or {}),
        "created_at": now,
        "updated_at": now,
        "project": project,
    }
    stmt = sqlite_insert(ManagedProvider.__table__).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["provider_id"],
        set_={
            "provider_type": stmt.excluded.provider_type,
            "api_key_encrypted": stmt.excluded.api_key_encrypted,
            "base_url": stmt.excluded.base_url,
            "extra_config": stmt.excluded.extra_config,
            "updated_at": stmt.excluded.updated_at,
            "project": stmt.excluded.project,
        },
    )
    await session.execute(stmt)
    await session.commit()


async def delete_provider(session: AsyncSession, provider_id: str) -> bool:
    """Delete one managed provider row. True when a row was removed."""
    result = await session.execute(
        delete(ManagedProvider).where(ManagedProvider.provider_id == provider_id)
    )
    await session.commit()
    return (result.rowcount or 0) > 0


async def rotate_credentials(
    session: AsyncSession, *, time_now: float | None = None
) -> dict[str, Any]:
    """Re-encrypt every managed_providers row under the current Fernet key."""
    now = time_now if time_now is not None else time.time()
    rotated = 0
    skipped_empty = 0
    failed: list[str] = []

    result = await session.execute(select(ManagedProvider))
    for provider in result.scalars().all():
        if not provider.api_key_encrypted:
            skipped_empty += 1
            continue
        try:
            new_ciphertext = rotate_token(provider.api_key_encrypted)
        except ValueError:
            failed.append(provider.provider_id)
            continue
        provider.api_key_encrypted = new_ciphertext
        provider.updated_at = now
        session.add(provider)
        rotated += 1
    await session.commit()
    return {"rotated": rotated, "skipped_empty": skipped_empty, "failed": failed}


__all__ = [
    "delete_provider",
    "get_provider",
    "list_providers",
    "rotate_credentials",
    "upsert_provider",
]
