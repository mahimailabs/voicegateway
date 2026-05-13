"""Symmetric encryption for secrets stored in the managed_* tables."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

_SECRET_FILE = Path.home() / ".config" / "voicegateway" / ".secret"
_FALLBACK_ENV = "VOICEGW_SECRET_FALLBACK"

_fernet: MultiFernet | None = None


def get_secret() -> bytes:
    """Return the primary Fernet key, from env or secret file."""
    env_secret = os.environ.get("VOICEGW_SECRET")
    if env_secret:
        return env_secret.encode()

    if _SECRET_FILE.exists():
        _SECRET_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)  # enforce 0600
        return _SECRET_FILE.read_bytes().strip()

    key = Fernet.generate_key()
    _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        str(_SECRET_FILE.parent / ".secret.tmp"),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        stat.S_IRUSR | stat.S_IWUSR,  # 0600 from the start
    )
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    os.replace(str(_SECRET_FILE.parent / ".secret.tmp"), str(_SECRET_FILE))
    return key


def _get_fallback_secrets() -> list[bytes]:
    """Return the parsed VOICEGW_SECRET_FALLBACK keys, or [] when unset."""
    raw = os.environ.get(_FALLBACK_ENV)
    if not raw:
        return []
    return [chunk.strip().encode() for chunk in raw.split(",") if chunk.strip()]


def _build_fernet() -> MultiFernet:
    """Construct the MultiFernet from primary + fallback keys."""
    keys = [Fernet(get_secret())]
    for fallback in _get_fallback_secrets():
        keys.append(Fernet(fallback))
    return MultiFernet(keys)


def _get_fernet() -> MultiFernet:
    global _fernet  # noqa: PLW0603
    if _fernet is None:
        _fernet = _build_fernet()
    return _fernet


def reset_fernet() -> None:
    """Clear the cached Fernet instance (for testing)."""
    global _fernet  # noqa: PLW0603
    _fernet = None


def encrypt(plaintext: str) -> str:
    """Encrypt a string. Empty input returns empty string."""
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a string. Empty input returns empty string."""
    if not ciphertext:
        return ""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise ValueError(
            "Failed to decrypt managed credential. "
            "This typically means VOICEGW_SECRET changed since the value was stored. "
            "Set VOICEGW_SECRET_FALLBACK to the previous key and run "
            "`voicegw rotate-secret` to re-encrypt under the new primary."
        ) from e


def rotate_token(ciphertext: str) -> str:
    """Re-encrypt ``ciphertext`` under the current primary key."""
    if not ciphertext:
        return ""
    try:
        return _get_fernet().rotate(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise ValueError(
            "Cannot rotate token: no configured key decrypts it. "
            "The original encryption secret is missing from both "
            "VOICEGW_SECRET and VOICEGW_SECRET_FALLBACK."
        ) from e


def is_fernet_token(value: str) -> bool:
    """Return True if the value looks like a valid Fernet ciphertext."""
    if not value:
        return False
    try:
        _get_fernet().decrypt(value.encode())
        return True
    except (InvalidToken, Exception):  # noqa: BLE001
        return False


def mask(value: str) -> str:
    """Mask a secret for display: 'secret-abc12345' -> 'secr...2345'."""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"
