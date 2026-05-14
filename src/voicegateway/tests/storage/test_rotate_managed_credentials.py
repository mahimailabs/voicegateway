"""Tests for StorageService.rotate_managed_credentials."""

from __future__ import annotations

import aiosqlite
import pytest
from cryptography.fernet import Fernet

from voicegateway.core.crypto import (
    decrypt,
    is_fernet_token,
    reset_fernet,
)
from voicegateway.services.storage_service import StorageService


@pytest.fixture(autouse=True)
def _reset_crypto(monkeypatch, tmp_path):
    reset_fernet()
    secret_file = tmp_path / ".secret"
    monkeypatch.setattr("voicegateway.core.crypto._SECRET_FILE", secret_file)
    monkeypatch.delenv("VOICEGW_SECRET", raising=False)
    monkeypatch.delenv("VOICEGW_SECRET_FALLBACK", raising=False)
    yield
    reset_fernet()


def _generate_key() -> str:
    return Fernet.generate_key().decode()


async def _seed_provider_rows(storage: StorageService) -> None:
    await storage.upsert_managed_provider(
        provider_id="tony-pizza:openai",
        provider_type="openai",
        api_key="sk-tony-openai",
        project="tony-pizza",
    )
    await storage.upsert_managed_provider(
        provider_id="mama-diner:deepgram",
        provider_type="deepgram",
        api_key="sk-mama-dg",
        project="mama-diner",
    )


async def test_rotate_re_encrypts_every_row_under_new_primary(monkeypatch, tmp_path):
    """End-to-end: write rows under key A, rotate to key B with A as"""
    old_key = _generate_key()
    monkeypatch.setenv("VOICEGW_SECRET", old_key)
    reset_fernet()

    storage = StorageService(str(tmp_path / "rotate.db"))
    await _seed_provider_rows(storage)

    # Snapshot the encrypted-under-A ciphertexts.
    before = {
        r["provider_id"]: r["api_key_encrypted"]
        for r in await storage.list_managed_providers()
    }
    assert all(is_fernet_token(ct) for ct in before.values())

    # Stage rotation: B is new primary, A is fallback.
    new_key = _generate_key()
    monkeypatch.setenv("VOICEGW_SECRET", new_key)
    monkeypatch.setenv("VOICEGW_SECRET_FALLBACK", old_key)
    reset_fernet()

    summary = await storage.rotate_managed_credentials()
    assert summary["rotated"] == 2
    assert summary["skipped_empty"] == 0
    assert summary["failed"] == []

    # Verify the rows now decrypt under B alone.
    monkeypatch.delenv("VOICEGW_SECRET_FALLBACK", raising=False)
    reset_fernet()

    after = {
        r["provider_id"]: r["api_key_encrypted"]
        for r in await storage.list_managed_providers()
    }
    assert decrypt(after["tony-pizza:openai"]) == "sk-tony-openai"
    assert decrypt(after["mama-diner:deepgram"]) == "sk-mama-dg"
    # Ciphertexts changed (the new primary uses a fresh IV per
    # encrypt; more importantly, the keying material differs).
    assert after["tony-pizza:openai"] != before["tony-pizza:openai"]
    assert after["mama-diner:deepgram"] != before["mama-diner:deepgram"]


async def test_rotate_skips_rows_with_empty_api_key(monkeypatch, tmp_path):
    """An empty api_key column has no ciphertext to rotate; the"""
    monkeypatch.setenv("VOICEGW_SECRET", _generate_key())
    reset_fernet()

    storage = StorageService(str(tmp_path / "rotate.db"))
    await storage.upsert_managed_provider(
        provider_id="empty:openai",
        provider_type="openai",
        api_key="",
        project="empty",
    )
    await storage.upsert_managed_provider(
        provider_id="real:openai",
        provider_type="openai",
        api_key="sk-real",
        project="real",
    )

    summary = await storage.rotate_managed_credentials()
    assert summary["rotated"] == 1
    assert summary["skipped_empty"] == 1
    assert summary["failed"] == []

    rows = {r["provider_id"]: r for r in await storage.list_managed_providers()}
    assert rows["empty:openai"]["api_key_encrypted"] == ""
    assert decrypt(rows["real:openai"]["api_key_encrypted"]) == "sk-real"


async def test_rotate_records_rows_that_fail_to_decrypt(monkeypatch, tmp_path):
    """A row encrypted under a key that is no longer in primary or"""
    primary_a = _generate_key()
    monkeypatch.setenv("VOICEGW_SECRET", primary_a)
    reset_fernet()

    storage = StorageService(str(tmp_path / "rotate.db"))
    await storage.upsert_managed_provider(
        provider_id="healthy:openai",
        provider_type="openai",
        api_key="sk-healthy",
        project="healthy",
    )
    await storage.upsert_managed_provider(
        provider_id="orphaned:deepgram",
        provider_type="deepgram",
        api_key="placeholder",
        project="orphaned",
    )

    # Replace the orphaned row's ciphertext with a token encrypted
    # under a key that will never appear in primary or fallback.
    orphan_key = Fernet.generate_key()
    orphan_token = Fernet(orphan_key).encrypt(b"who-knows").decode()
    db = await aiosqlite.connect(storage._db_path)
    try:
        await db.execute(
            "UPDATE managed_providers SET api_key_encrypted = ? "
            "WHERE provider_id = 'orphaned:deepgram'",
            (orphan_token,),
        )
        await db.commit()
    finally:
        await db.close()

    # Rotate to a new primary, with A as the only fallback. Healthy
    # row decrypts via fallback; orphaned row's key is missing from
    # both primary and fallback so it lands in `failed`.
    primary_b = _generate_key()
    monkeypatch.setenv("VOICEGW_SECRET", primary_b)
    monkeypatch.setenv("VOICEGW_SECRET_FALLBACK", primary_a)
    reset_fernet()

    summary = await storage.rotate_managed_credentials()
    assert summary["rotated"] == 1
    assert summary["skipped_empty"] == 0
    assert summary["failed"] == ["orphaned:deepgram"]

    # Healthy row's plaintext survived the rotation.
    after = {
        r["provider_id"]: r["api_key_encrypted"]
        for r in await storage.list_managed_providers()
    }
    assert decrypt(after["healthy:openai"]) == "sk-healthy"
    # The orphaned row's stored ciphertext is unchanged.
    assert after["orphaned:deepgram"] == orphan_token


async def test_rotate_returns_zeros_on_empty_table(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICEGW_SECRET", _generate_key())
    reset_fernet()
    storage = StorageService(str(tmp_path / "rotate.db"))

    summary = await storage.rotate_managed_credentials()
    assert summary == {"rotated": 0, "skipped_empty": 0, "failed": []}
