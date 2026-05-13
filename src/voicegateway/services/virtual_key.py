"""Service for issuing, verifying and revoking virtual keys.

Domain rules:

* The plaintext is generated and shown exactly once on create.
* The hash stored at rest is bcrypt-cost-12.
* A revoked row stays in the table for audit; it just stops authenticating.
* ``verify`` is constant-time per candidate: bcrypt is the only check that
  decides authenticity.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Final

import bcrypt

from voicegateway.models.virtual_key import VirtualKey
from voicegateway.repository.virtual_key import VirtualKeyRepository

VK_PREFIX: Final[str] = "vk_"
_VISIBLE_PREFIX_LEN: Final[int] = 8  # ``vk_`` + 5 random chars
_RANDOM_SUFFIX_LEN: Final[int] = 32
_BCRYPT_COST: Final[int] = 12
_BASE32_ALPHABET: Final[str] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


@dataclass(frozen=True)
class CreatedKey:
    """Service-level return type: plaintext shown once, row safe to log."""

    plaintext: str
    row: VirtualKey


@dataclass(frozen=True)
class VerifiedKey:
    """Service-level return type for a successful :meth:`verify` call."""

    id: int
    tenant_id: str | None
    name: str


def _generate_plaintext_key() -> str:
    suffix = "".join(
        secrets.choice(_BASE32_ALPHABET) for _ in range(_RANDOM_SUFFIX_LEN)
    )
    return f"{VK_PREFIX}{suffix}"


def _visible_prefix(plaintext: str) -> str:
    return plaintext[:_VISIBLE_PREFIX_LEN]


def _hash(plaintext: str) -> str:
    digest = bcrypt.hashpw(
        plaintext.encode("utf-8"), bcrypt.gensalt(rounds=_BCRYPT_COST)
    )
    return digest.decode("utf-8")


def _check(plaintext: str, stored_hash: str) -> bool:
    return bcrypt.checkpw(plaintext.encode("utf-8"), stored_hash.encode("utf-8"))


class VirtualKeyService:
    """Composes :class:`VirtualKeyRepository` with the bcrypt / plaintext logic."""

    def __init__(self, repository: VirtualKeyRepository) -> None:
        self._repo = repository

    async def create_key(
        self,
        *,
        name: str,
        tenant_id: str | None = None,
        issued_by: str | None = None,
    ) -> CreatedKey:
        """Mint a new virtual key. Returns the plaintext exactly once."""
        if not name:
            raise ValueError("name must be non-empty")
        plaintext = _generate_plaintext_key()
        row = VirtualKey(
            key_prefix=_visible_prefix(plaintext),
            key_hash=_hash(plaintext),
            name=name,
            tenant_id=tenant_id,
            issued_by=issued_by,
        )
        persisted = await self._repo.create(row)
        return CreatedKey(plaintext=plaintext, row=persisted)

    async def get_key(self, key_id: int) -> VirtualKey:
        """Fetch one key by id. Raises :class:`NotFoundError` when missing."""
        return await self._repo.read_by_id(key_id)

    async def list_keys(self, *, include_revoked: bool = True) -> list[VirtualKey]:
        """Return all keys, newest first."""
        return await self._repo.list_keys(include_revoked=include_revoked)

    async def verify(self, plaintext: str) -> VerifiedKey | None:
        """Validate a plaintext key against the stored hashes."""
        if not plaintext.startswith(VK_PREFIX):
            return None
        candidates = await self._repo.find_by_prefix(_visible_prefix(plaintext))
        for row in candidates:
            if row.revoked_at is not None:
                continue
            if _check(plaintext, row.key_hash):
                assert row.id is not None
                return VerifiedKey(id=row.id, tenant_id=row.tenant_id, name=row.name)
        return None

    async def mark_used(self, key_id: int) -> None:
        """Bump ``last_used_at``. Idempotent."""
        await self._repo.mark_used(key_id)

    async def revoke(self, key_id: int) -> bool:
        """Soft-revoke. Returns True on transition."""
        await self._repo.read_by_id(key_id)  # 404 surface before revoke attempt
        return await self._repo.revoke(key_id)

    async def list_stale(self, *, stale_after_days: int) -> list[VirtualKey]:
        """Non-revoked keys past the staleness cutoff."""
        return await self._repo.list_stale(stale_after_days=stale_after_days)
