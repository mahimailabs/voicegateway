"""Tests for voicegateway/core/crypto.py."""

from __future__ import annotations

import os
import stat

import pytest

from voicegateway.core.crypto import (
    decrypt,
    encrypt,
    get_secret,
    is_fernet_token,
    mask,
    reset_fernet,
)


@pytest.fixture(autouse=True)
def _reset_crypto(monkeypatch, tmp_path):
    """Isolate each test from shared crypto state."""
    reset_fernet()
    secret_file = tmp_path / ".secret"
    monkeypatch.setattr("voicegateway.core.crypto._SECRET_FILE", secret_file)
    monkeypatch.delenv("VOICEGW_SECRET", raising=False)
    yield
    reset_fernet()


def test_encrypt_decrypt_roundtrip():
    plaintext = "sk-my-super-secret-api-key"
    ciphertext = encrypt(plaintext)
    assert ciphertext != plaintext
    assert decrypt(ciphertext) == plaintext


def test_empty_string_passthrough():
    assert encrypt("") == ""
    assert decrypt("") == ""


def test_wrong_key_raises(monkeypatch, tmp_path):
    ciphertext = encrypt("secret-data")

    # Change the key
    reset_fernet()
    from cryptography.fernet import Fernet
    monkeypatch.setenv("VOICEGW_SECRET", Fernet.generate_key().decode())

    with pytest.raises(ValueError, match="Failed to decrypt"):
        decrypt(ciphertext)


def test_mask_long():
    assert mask("sk-abc123xyz4f8a") == "sk-a...4f8a"


def test_mask_short():
    assert mask("short") == "*****"


def test_mask_empty():
    assert mask("") == ""


def test_secret_file_created_with_600_perms(tmp_path, monkeypatch):
    secret_file = tmp_path / ".secret"
    monkeypatch.setattr("voicegateway.core.crypto._SECRET_FILE", secret_file)
    monkeypatch.delenv("VOICEGW_SECRET", raising=False)

    key = get_secret()
    assert secret_file.exists()
    assert len(key) > 0
    mode = secret_file.stat().st_mode
    assert mode & stat.S_IRWXU == stat.S_IRUSR | stat.S_IWUSR  # 0600
    assert mode & stat.S_IRWXG == 0  # no group
    assert mode & stat.S_IRWXO == 0  # no other


def test_env_var_overrides_secret_file(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    env_key = Fernet.generate_key().decode()
    monkeypatch.setenv("VOICEGW_SECRET", env_key)

    result = get_secret()
    assert result == env_key.encode()


def test_secret_file_reused(tmp_path, monkeypatch):
    secret_file = tmp_path / ".secret"
    monkeypatch.setattr("voicegateway.core.crypto._SECRET_FILE", secret_file)

    key1 = get_secret()
    reset_fernet()
    key2 = get_secret()
    assert key1 == key2


def test_is_fernet_token():
    ct = encrypt("hello")
    assert is_fernet_token(ct) is True
    assert is_fernet_token("not-a-token") is False
    assert is_fernet_token("") is False
