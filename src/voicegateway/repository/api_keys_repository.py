"""Async function-style repo for the ``api_keys`` table (ORM).

The ORM class-based :class:`voicegateway.repository.api_key_repository.ApiKeyRepository`
is used by FastAPI admin endpoints through the DI container. This
function-style module remains the authentication-runtime path (used
by :mod:`voicegateway.core.auth` to verify ``Bearer vk_…`` headers
and by the CLI for create/revoke/list operations). Both back the same
``api_keys`` table; this module's bodies now use AsyncSession +
SQLAlchemy text() so they coexist cleanly on the unified ORM stack.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import bcrypt
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_VK_PREFIX: Final[str] = "vk_"
_VISIBLE_PREFIX_LEN: Final[int] = 8  # ``vk_`` + 5 random chars
_RANDOM_SUFFIX_LEN: Final[int] = 32
_BCRYPT_COST: Final[int] = 12
_BASE32_ALPHABET: Final[str] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


@dataclass(frozen=True)
class ApiKeyRow:
    """One row from the ``api_keys`` table (without ``key_hash``)."""

    id: int
    key_prefix: str
    name: str
    tenant_id: str | None
    issued_by: str | None
    issued_at: str
    last_used_at: str | None
    revoked_at: str | None


@dataclass(frozen=True)
class CreatedApiKey:
    """Return value of :func:`create_api_key`."""

    id: int
    plaintext: str
    row: ApiKeyRow


@dataclass(frozen=True)
class VerifiedKey:
    """Result of a successful :func:`verify` call."""

    id: int
    tenant_id: str | None
    name: str
    role: str = "tenant"
    scopes: str = "*"

    def has_scope(self, required: str) -> bool:
        """Return True if this key covers ``required``."""
        # Lazy import to avoid core.auth -> repository -> core.auth cycle.
        from voicegateway.core.auth import WILDCARD_SCOPE  # noqa: PLC0415

        csv = [s.strip() for s in self.scopes.split(",") if s.strip()]
        return WILDCARD_SCOPE in csv or required in csv


def _generate_plaintext_key() -> str:
    suffix = "".join(
        secrets.choice(_BASE32_ALPHABET) for _ in range(_RANDOM_SUFFIX_LEN)
    )
    return f"{_VK_PREFIX}{suffix}"


def _visible_prefix(plaintext: str) -> str:
    return plaintext[:_VISIBLE_PREFIX_LEN]


def _hash_key(plaintext: str) -> str:
    digest = bcrypt.hashpw(
        plaintext.encode("utf-8"), bcrypt.gensalt(rounds=_BCRYPT_COST)
    )
    return digest.decode("utf-8")


def _check_key(plaintext: str, stored_hash: str) -> bool:
    return bcrypt.checkpw(plaintext.encode("utf-8"), stored_hash.encode("utf-8"))


def _row_to_dataclass(row) -> ApiKeyRow:
    return ApiKeyRow(
        id=int(row[0]),
        key_prefix=str(row[1]),
        name=str(row[2]),
        tenant_id=None if row[3] is None else str(row[3]),
        issued_by=None if row[4] is None else str(row[4]),
        issued_at=str(row[5]),
        last_used_at=None if row[6] is None else str(row[6]),
        revoked_at=None if row[7] is None else str(row[7]),
    )


_SELECT_ROW_FIELDS = (
    "id, key_prefix, name, tenant_id, issued_by, issued_at, last_used_at, revoked_at"
)


async def create_api_key(
    session: AsyncSession,
    *,
    name: str,
    tenant_id: str | None = None,
    issued_by: str | None = None,
    role: str = "tenant",
    scopes: str = "*",
) -> CreatedApiKey:
    """Create a virtual key and return the plaintext once."""
    if not name:
        raise ValueError("name must be non-empty")
    plaintext = _generate_plaintext_key()
    prefix = _visible_prefix(plaintext)
    digest = _hash_key(plaintext)
    result = await session.execute(
        text(
            "INSERT INTO api_keys ("
            "key_prefix, key_hash, name, tenant_id, issued_by, role, scopes, issued_at"
            ") VALUES ("
            ":prefix, :digest, :name, :tenant_id, :issued_by, :role, :scopes,"
            " CURRENT_TIMESTAMP"
            ")"
        ),
        {
            "prefix": prefix,
            "digest": digest,
            "name": name,
            "tenant_id": tenant_id,
            "issued_by": issued_by,
            "role": role,
            "scopes": scopes,
        },
    )
    await session.commit()
    new_id = result.lastrowid  # type: ignore[attr-defined]
    if new_id is None:
        raise RuntimeError("INSERT into api_keys did not return a row id")
    row = await get_by_id(session, new_id)
    if row is None:
        raise RuntimeError(f"api_keys row {new_id} disappeared after INSERT")
    return CreatedApiKey(id=new_id, plaintext=plaintext, row=row)


async def get_by_id(session: AsyncSession, key_id: int) -> ApiKeyRow | None:
    """Return the row for ``key_id`` or ``None`` if not found."""
    result = await session.execute(
        text(f"SELECT {_SELECT_ROW_FIELDS} FROM api_keys WHERE id = :key_id"),
        {"key_id": key_id},
    )
    row = result.fetchone()
    return _row_to_dataclass(row) if row is not None else None


async def list_keys(
    session: AsyncSession,
    *,
    include_revoked: bool = True,
) -> list[ApiKeyRow]:
    """Return all virtual keys, newest first."""
    where = "" if include_revoked else "WHERE revoked_at IS NULL "
    result = await session.execute(
        text(
            f"SELECT {_SELECT_ROW_FIELDS} FROM api_keys "
            f"{where}ORDER BY issued_at DESC, id DESC"
        )
    )
    return [_row_to_dataclass(row) for row in result]


async def verify(session: AsyncSession, plaintext: str) -> VerifiedKey | None:
    """Validate ``plaintext`` against the stored hashes."""
    if not plaintext.startswith(_VK_PREFIX):
        return None
    prefix = _visible_prefix(plaintext)
    result = await session.execute(
        text(
            "SELECT id, key_hash, tenant_id, name, revoked_at, role, scopes "
            "FROM api_keys WHERE key_prefix = :prefix"
        ),
        {"prefix": prefix},
    )
    for row in result:
        key_id, stored_hash, tenant_id, name, revoked_at, role, scopes = (
            row[0],
            row[1],
            row[2],
            row[3],
            row[4],
            row[5],
            row[6],
        )
        if revoked_at is not None:
            continue
        if _check_key(plaintext, str(stored_hash)):
            return VerifiedKey(
                id=int(key_id),
                tenant_id=None if tenant_id is None else str(tenant_id),
                name=str(name),
                role=str(role) if role is not None else "tenant",
                scopes=str(scopes) if scopes is not None else "*",
            )
    return None


async def mark_used(session: AsyncSession, key_id: int) -> None:
    """Bump ``last_used_at`` to the current timestamp."""
    await session.execute(
        text("UPDATE api_keys SET last_used_at = CURRENT_TIMESTAMP WHERE id = :key_id"),
        {"key_id": key_id},
    )
    await session.commit()


async def revoke(session: AsyncSession, key_id: int) -> bool:
    """Soft-revoke the key (OQ5 lock)."""
    result = await session.execute(
        text(
            "UPDATE api_keys SET revoked_at = CURRENT_TIMESTAMP "
            "WHERE id = :key_id AND revoked_at IS NULL"
        ),
        {"key_id": key_id},
    )
    await session.commit()
    return (result.rowcount or 0) > 0  # type: ignore[attr-defined]


async def list_stale(
    session: AsyncSession, *, stale_after_days: int
) -> list[ApiKeyRow]:
    """Return non-revoked keys whose ``last_used_at`` is older than the threshold."""
    if stale_after_days < 0:
        raise ValueError(f"stale_after_days must be >= 0, got {stale_after_days}")
    # SQLite spells "now minus N days" datetime('now', '-N days'); Postgres has
    # no datetime(), so use CURRENT_TIMESTAMP - make_interval(days => N).
    try:
        dialect = session.bind.dialect.name
    except Exception:  # noqa: BLE001 - default to the SQLite spelling
        dialect = "sqlite"
    if dialect == "postgresql":
        cutoff_sql = "CURRENT_TIMESTAMP - make_interval(days => :days)"
        params: dict[str, object] = {"days": stale_after_days}
    else:
        cutoff_sql = "datetime('now', :delta || ' days')"
        params = {"delta": f"-{stale_after_days}"}
    result = await session.execute(
        text(
            f"SELECT {_SELECT_ROW_FIELDS} FROM api_keys "
            "WHERE revoked_at IS NULL "
            f"AND COALESCE(last_used_at, issued_at) <= {cutoff_sql} "
            "ORDER BY COALESCE(last_used_at, issued_at) ASC, id ASC"
        ),
        params,
    )
    return [_row_to_dataclass(row) for row in result]


__all__ = [
    "CreatedApiKey",
    "VerifiedKey",
    "ApiKeyRow",
    "create_api_key",
    "get_by_id",
    "list_keys",
    "list_stale",
    "mark_used",
    "revoke",
    "verify",
]
