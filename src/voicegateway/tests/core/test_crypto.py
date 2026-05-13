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
    rotate_token,
)


@pytest.fixture(autouse=True)
def _reset_crypto(monkeypatch, tmp_path):
    """Isolate each test from shared crypto state."""
    reset_fernet()
    secret_file = tmp_path / ".secret"
    monkeypatch.setattr("voicegateway.core.crypto._SECRET_FILE", secret_file)
    monkeypatch.delenv("VOICEGW_SECRET", raising=False)
    monkeypatch.delenv("VOICEGW_SECRET_FALLBACK", raising=False)
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
    assert mask("secret-abc12345") == "secr...2345"


def test_mask_short():
    assert mask("short") == "*****"


def test_mask_empty():
    assert mask("") == ""


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX file permissions not available on Windows"
)
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


# ---------------------------------------------------------------------------
# MultiFernet rotation (Q10)
# ---------------------------------------------------------------------------


def _generate_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def test_fallback_decrypts_old_token(monkeypatch):
    """A token encrypted under key A is still decryptable when A is
    moved to VOICEGW_SECRET_FALLBACK and a new key B becomes the
    primary VOICEGW_SECRET. Critical for the rotation window between
    rolling out the new key and running ``voicegw rotate-secret``.
    """
    old_key = _generate_key()
    monkeypatch.setenv("VOICEGW_SECRET", old_key)
    reset_fernet()
    ciphertext = encrypt("sk-original")

    # Operator stages a rotation: new primary, old key as fallback.
    new_key = _generate_key()
    monkeypatch.setenv("VOICEGW_SECRET", new_key)
    monkeypatch.setenv("VOICEGW_SECRET_FALLBACK", old_key)
    reset_fernet()

    assert decrypt(ciphertext) == "sk-original"


def test_decrypt_fails_when_neither_primary_nor_fallback_match(monkeypatch):
    monkeypatch.setenv("VOICEGW_SECRET", _generate_key())
    reset_fernet()
    ciphertext = encrypt("secret")

    monkeypatch.setenv("VOICEGW_SECRET", _generate_key())
    monkeypatch.setenv("VOICEGW_SECRET_FALLBACK", _generate_key())
    reset_fernet()

    with pytest.raises(ValueError, match="rotate-secret"):
        decrypt(ciphertext)


def test_multiple_fallback_keys_separated_by_commas(monkeypatch):
    """VOICEGW_SECRET_FALLBACK accepts a comma-separated list so an
    operator can stage two consecutive rotations without losing
    access to the oldest tokens.
    """
    oldest = _generate_key()
    middle = _generate_key()
    newest = _generate_key()

    monkeypatch.setenv("VOICEGW_SECRET", oldest)
    reset_fernet()
    oldest_ct = encrypt("oldest-data")

    monkeypatch.setenv("VOICEGW_SECRET", middle)
    monkeypatch.setenv("VOICEGW_SECRET_FALLBACK", oldest)
    reset_fernet()
    middle_ct = encrypt("middle-data")

    monkeypatch.setenv("VOICEGW_SECRET", newest)
    monkeypatch.setenv("VOICEGW_SECRET_FALLBACK", f"{middle},{oldest}")
    reset_fernet()

    assert decrypt(oldest_ct) == "oldest-data"
    assert decrypt(middle_ct) == "middle-data"
    new_ct = encrypt("newest-data")
    assert decrypt(new_ct) == "newest-data"


def test_fallback_env_with_trailing_comma_is_tolerated(monkeypatch):
    old_key = _generate_key()
    monkeypatch.setenv("VOICEGW_SECRET", old_key)
    reset_fernet()
    ciphertext = encrypt("payload")

    monkeypatch.setenv("VOICEGW_SECRET", _generate_key())
    monkeypatch.setenv("VOICEGW_SECRET_FALLBACK", f"{old_key},")  # trailing comma
    reset_fernet()

    assert decrypt(ciphertext) == "payload"


def test_rotate_token_re_encrypts_under_primary(monkeypatch):
    """rotate_token() decrypts via any configured key and re-encrypts
    via the primary, returning a token the new primary alone can
    read. After rotation, removing the fallback no longer breaks
    decrypt.
    """
    old_key = _generate_key()
    monkeypatch.setenv("VOICEGW_SECRET", old_key)
    reset_fernet()
    old_ct = encrypt("rotate-me")

    new_key = _generate_key()
    monkeypatch.setenv("VOICEGW_SECRET", new_key)
    monkeypatch.setenv("VOICEGW_SECRET_FALLBACK", old_key)
    reset_fernet()

    new_ct = rotate_token(old_ct)
    assert new_ct != old_ct
    assert decrypt(new_ct) == "rotate-me"

    # Operator now removes the fallback. The rotated ciphertext
    # still decrypts under the new primary alone.
    monkeypatch.delenv("VOICEGW_SECRET_FALLBACK", raising=False)
    reset_fernet()
    assert decrypt(new_ct) == "rotate-me"


def test_rotate_token_passthrough_on_empty():
    assert rotate_token("") == ""


def test_rotate_token_raises_when_no_key_decrypts(monkeypatch):
    monkeypatch.setenv("VOICEGW_SECRET", _generate_key())
    reset_fernet()
    ciphertext = encrypt("x")

    # Cycle to an unrelated primary, no fallback at all.
    monkeypatch.setenv("VOICEGW_SECRET", _generate_key())
    reset_fernet()

    with pytest.raises(ValueError, match="VOICEGW_SECRET_FALLBACK"):
        rotate_token(ciphertext)


def test_encrypt_uses_primary_when_fallback_set(monkeypatch):
    """A token freshly encrypted with both primary and fallback set
    must decrypt under the primary alone afterwards. Guards against
    accidentally encrypting under the fallback.
    """
    old_key = _generate_key()
    new_key = _generate_key()
    monkeypatch.setenv("VOICEGW_SECRET", new_key)
    monkeypatch.setenv("VOICEGW_SECRET_FALLBACK", old_key)
    reset_fernet()
    ciphertext = encrypt("fresh")

    monkeypatch.delenv("VOICEGW_SECRET_FALLBACK", raising=False)
    reset_fernet()
    assert decrypt(ciphertext) == "fresh"
