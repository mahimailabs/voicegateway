"""Service for issuing, verifying and revoking virtual keys."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Final

import bcrypt

from voicegateway.models.virtual_key_model import VirtualKey
from voicegateway.repository.virtual_key_repository import VirtualKeyRepository
from voicegateway.services.base_service import BaseService

VK_PREFIX: Final[str] = "vk_"
_VISIBLE_PREFIX_LEN: Final[int] = 8
_RANDOM_SUFFIX_LEN: Final[int] = 32
_BCRYPT_COST: Final[int] = 12
_BASE32_ALPHABET: Final[str] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


@dataclass(frozen=True)
class CreatedKey:
    """Plaintext shown once on create, row safe to log."""

    plaintext: str
    row: VirtualKey


@dataclass(frozen=True)
class VerifiedKey:
    """Result of a successful verify call."""

    id: int
    tenant_id: str | None
    name: str


def _generate_plaintext_key() -> str:
    """Generate a fresh `vk_` + 32 base32 plaintext."""
    suffix = "".join(
        secrets.choice(_BASE32_ALPHABET) for _ in range(_RANDOM_SUFFIX_LEN)
    )
    return f"{VK_PREFIX}{suffix}"


def _visible_prefix(plaintext: str) -> str:
    """Return the first 8 chars used as the row prefix index."""
    return plaintext[:_VISIBLE_PREFIX_LEN]


def _hash(plaintext: str) -> str:
    """Bcrypt-hash the plaintext at cost 12."""
    digest = bcrypt.hashpw(
        plaintext.encode("utf-8"), bcrypt.gensalt(rounds=_BCRYPT_COST)
    )
    return digest.decode("utf-8")


def _check(plaintext: str, stored_hash: str) -> bool:
    """Constant-time bcrypt comparison."""
    return bcrypt.checkpw(plaintext.encode("utf-8"), stored_hash.encode("utf-8"))


class VirtualKeyService(BaseService[VirtualKey]):
    """Composes VirtualKeyRepository with bcrypt + plaintext semantics."""

    def __init__(self, repository: VirtualKeyRepository) -> None:
        super().__init__(repository)
        # Re-typed alias for the concrete repo so domain methods get IDE help.
        self._repository: VirtualKeyRepository = repository

    async def create_key(
        self,
        *,
        name: str,
        tenant_id: str | None = None,
        issued_by: str | None = None,
    ) -> CreatedKey:
        """Mint a new virtual key. The plaintext is returned exactly once."""
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
        persisted = await self._repository.create(row)
        return CreatedKey(plaintext=plaintext, row=persisted)

    async def list_keys(self, *, include_revoked: bool = True) -> list[VirtualKey]:
        """Return all keys, newest first."""
        return await self._repository.list_keys(include_revoked=include_revoked)

    async def verify(self, plaintext: str) -> VerifiedKey | None:
        """Validate a plaintext key against the stored hashes."""
        if not plaintext.startswith(VK_PREFIX):
            return None
        candidates = await self._repository.find_by_prefix(_visible_prefix(plaintext))
        for row in candidates:
            if row.revoked_at is not None:
                continue
            if _check(plaintext, row.key_hash):
                assert row.id is not None
                return VerifiedKey(id=row.id, tenant_id=row.tenant_id, name=row.name)
        return None

    async def mark_used(self, key_id: int) -> None:
        """Bump last_used_at on the row. Idempotent."""
        await self._repository.mark_used(key_id)

    async def revoke(self, key_id: int) -> bool:
        """Soft-revoke. Surfaces 404 if the row is missing, else returns True on transition."""
        await self._repository.read_by_id(key_id)
        return await self._repository.revoke(key_id)

    async def list_stale(self, *, stale_after_days: int) -> list[VirtualKey]:
        """Non-revoked keys past the staleness cutoff."""
        return await self._repository.list_stale(stale_after_days=stale_after_days)
