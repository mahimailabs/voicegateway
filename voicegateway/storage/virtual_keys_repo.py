"""Async repo for the ``virtual_keys`` table.

Implements REQ-VG-TENANT-003 (issue virtual API keys with optional
tenant scope, support revocation and stale-key detection).

The plaintext key is returned ONCE at issuance; only a bcrypt hash and
an 8-char visible prefix persist (OQ1 lock). Verification scans by
prefix to keep bcrypt comparisons O(1) per request rather than O(n);
the prefix collision space (~33M values from 5 base32 chars after
``vk_``) is large enough to avoid practical collision but small enough
to expose nothing about the secret.

Key format (locked at v0.4.0/T01 from Foundry OQ1):

* Total key handed to the user: ``vk_`` + 32 base32 random chars
  (``vk_AABBCCDDEEFFGGHHIIJJKKLLMMNNOOPP``) = 35 chars.
* Visible prefix stored in ``key_prefix``: first 8 chars
  (``vk_AABBC``) = ``vk_`` + 5 random.
* The full key is bcrypt-hashed (cost 12) and stored in ``key_hash``.

Revocation is soft (OQ5 lock): ``revoke(id)`` writes
``revoked_at = CURRENT_TIMESTAMP`` and keeps the row for audit + the
stale-key detector. Hard delete is out of scope for v0.4.0.

Mirrors the flat-function-module pattern of ``turns_repo.py`` and
``replay_repo.py``: each function takes an ``aiosqlite.Connection`` and
the caller owns the connection lifecycle.

Schema reference: ``voicegateway/storage/migrations/0005_tenant_attribution.py``.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import bcrypt

if TYPE_CHECKING:
    import aiosqlite


_VK_PREFIX: Final[str] = "vk_"
_VISIBLE_PREFIX_LEN: Final[int] = 8  # ``vk_`` + 5 random chars
_RANDOM_SUFFIX_LEN: Final[int] = 32
_BCRYPT_COST: Final[int] = 12
_BASE32_ALPHABET: Final[str] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


@dataclass(frozen=True)
class VirtualKeyRow:
    """One row from the ``virtual_keys`` table (without ``key_hash``).

    ``key_hash`` is omitted because callers never need it; the hash is
    consulted only inside :func:`verify`. Exposing the plaintext key is
    impossible: it is only ever returned from :func:`create_virtual_key`.
    """

    id: int
    key_prefix: str
    name: str
    tenant_id: str | None
    issued_by: str | None
    issued_at: str
    last_used_at: str | None
    revoked_at: str | None


@dataclass(frozen=True)
class CreatedVirtualKey:
    """Return value of :func:`create_virtual_key`.

    The ``plaintext`` field is the one and only time the full key is
    visible. Callers are expected to surface it to the dashboard's
    "show key once" modal (REQ-VG-TENANT-003 AC-2) and never persist it
    server-side beyond the bcrypt hash.
    """

    id: int
    plaintext: str
    row: VirtualKeyRow


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


_INSERT_KEY = (
    "INSERT INTO virtual_keys ("
    "key_prefix, key_hash, name, tenant_id, issued_by"
    ") VALUES (?, ?, ?, ?, ?)"
)


async def create_virtual_key(
    db: aiosqlite.Connection,
    *,
    name: str,
    tenant_id: str | None = None,
    issued_by: str | None = None,
) -> CreatedVirtualKey:
    """Create a virtual key and return the plaintext once.

    ``name`` is the human-readable label shown in the dashboard.
    ``tenant_id`` scopes the key (REQ-VG-TENANT-004): when the auth
    middleware presents a scoped key, it auto-tags the session with
    this tenant. ``None`` leaves the key unscoped so the body's
    ``tenant_id`` (if any) wins.
    ``issued_by`` is a free-form audit string (the dashboard sets it to
    the operator's identity).

    Commits the connection.
    """
    if not name:
        raise ValueError("name must be non-empty")
    plaintext = _generate_plaintext_key()
    prefix = _visible_prefix(plaintext)
    digest = _hash_key(plaintext)
    cursor = await db.execute(_INSERT_KEY, (prefix, digest, name, tenant_id, issued_by))
    await db.commit()
    new_id = cursor.lastrowid
    if new_id is None:
        # aiosqlite always reports lastrowid for INSERT; defensive only.
        raise RuntimeError("INSERT into virtual_keys did not return a row id")
    row = await get_by_id(db, new_id)
    if row is None:
        raise RuntimeError(f"virtual_keys row {new_id} disappeared after INSERT")
    return CreatedVirtualKey(id=new_id, plaintext=plaintext, row=row)


_SELECT_ROW_FIELDS = (
    "id, key_prefix, name, tenant_id, issued_by, issued_at, last_used_at, revoked_at"
)


def _row_to_dataclass(row: Sequence[Any]) -> VirtualKeyRow:
    return VirtualKeyRow(
        id=int(row[0]),
        key_prefix=str(row[1]),
        name=str(row[2]),
        tenant_id=None if row[3] is None else str(row[3]),
        issued_by=None if row[4] is None else str(row[4]),
        issued_at=str(row[5]),
        last_used_at=None if row[6] is None else str(row[6]),
        revoked_at=None if row[7] is None else str(row[7]),
    )


async def get_by_id(db: aiosqlite.Connection, key_id: int) -> VirtualKeyRow | None:
    """Return the row for ``key_id`` or ``None`` if not found."""
    cursor = await db.execute(
        f"SELECT {_SELECT_ROW_FIELDS} FROM virtual_keys WHERE id = ?",
        (key_id,),
    )
    row = await cursor.fetchone()
    return _row_to_dataclass(row) if row is not None else None


async def list_keys(
    db: aiosqlite.Connection,
    *,
    include_revoked: bool = True,
) -> list[VirtualKeyRow]:
    """Return all virtual keys, newest first.

    ``include_revoked=False`` filters out keys with a non-NULL
    ``revoked_at`` (used by the issuance flow to avoid showing
    tombstones unless the operator opts in).
    """
    where = "" if include_revoked else "WHERE revoked_at IS NULL "
    cursor = await db.execute(
        f"SELECT {_SELECT_ROW_FIELDS} FROM virtual_keys "
        f"{where}ORDER BY issued_at DESC, id DESC"
    )
    return [_row_to_dataclass(row) async for row in cursor]


@dataclass(frozen=True)
class VerifiedKey:
    """Result of a successful :func:`verify` call."""

    id: int
    tenant_id: str | None
    name: str


async def verify(db: aiosqlite.Connection, plaintext: str) -> VerifiedKey | None:
    """Validate ``plaintext`` against the stored hashes.

    Returns the row's ``id`` + ``tenant_id`` + ``name`` on a match,
    ``None`` on any failure (bad prefix, no row, hash mismatch, revoked).
    Revoked keys are rejected here so callers don't need a second check.

    The caller is responsible for invoking :func:`mark_used` on success
    if a ``last_used_at`` bump is desired; ``verify`` keeps the read
    side pure to make it safe to call from middleware that may rate-limit
    repeat failures.
    """
    if not plaintext.startswith(_VK_PREFIX):
        return None
    prefix = _visible_prefix(plaintext)
    cursor = await db.execute(
        "SELECT id, key_hash, tenant_id, name, revoked_at "
        "FROM virtual_keys WHERE key_prefix = ?",
        (prefix,),
    )
    rows = await cursor.fetchall()
    for row in rows:
        key_id, stored_hash, tenant_id, name, revoked_at = (
            row[0],
            row[1],
            row[2],
            row[3],
            row[4],
        )
        if revoked_at is not None:
            continue
        if _check_key(plaintext, str(stored_hash)):
            return VerifiedKey(
                id=int(key_id),
                tenant_id=None if tenant_id is None else str(tenant_id),
                name=str(name),
            )
    return None


async def mark_used(db: aiosqlite.Connection, key_id: int) -> None:
    """Bump ``last_used_at`` to the current timestamp.

    Idempotent. Commits the connection. Called by the auth middleware
    after a successful :func:`verify`.
    """
    await db.execute(
        "UPDATE virtual_keys SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
        (key_id,),
    )
    await db.commit()


async def revoke(db: aiosqlite.Connection, key_id: int) -> bool:
    """Soft-revoke the key (OQ5 lock).

    Sets ``revoked_at = CURRENT_TIMESTAMP``. Returns ``True`` if a row
    was updated, ``False`` if the row did not exist or was already
    revoked. Commits the connection.
    """
    cursor = await db.execute(
        "UPDATE virtual_keys "
        "SET revoked_at = CURRENT_TIMESTAMP "
        "WHERE id = ? AND revoked_at IS NULL",
        (key_id,),
    )
    await db.commit()
    return cursor.rowcount > 0


async def list_stale(
    db: aiosqlite.Connection, *, stale_after_days: int
) -> list[VirtualKeyRow]:
    """Return non-revoked keys whose ``last_used_at`` is older than the threshold.

    Keys that have never been used (``last_used_at IS NULL``) are
    considered stale if ``issued_at`` is older than the threshold:
    issued-but-never-used keys past the cutoff are exactly what the
    stale-key surface is for. The threshold is the project config's
    ``virtual_key_stale_days`` (T09).
    """
    if stale_after_days < 0:
        raise ValueError(f"stale_after_days must be >= 0, got {stale_after_days}")
    cursor = await db.execute(
        f"SELECT {_SELECT_ROW_FIELDS} FROM virtual_keys "
        "WHERE revoked_at IS NULL "
        "AND COALESCE(last_used_at, issued_at) <= "
        "    datetime('now', ? || ' days') "
        "ORDER BY COALESCE(last_used_at, issued_at) ASC, id ASC",
        (f"-{stale_after_days}",),
    )
    return [_row_to_dataclass(row) async for row in cursor]


__all__ = [
    "CreatedVirtualKey",
    "VerifiedKey",
    "VirtualKeyRow",
    "create_virtual_key",
    "get_by_id",
    "list_keys",
    "list_stale",
    "mark_used",
    "revoke",
    "verify",
]
