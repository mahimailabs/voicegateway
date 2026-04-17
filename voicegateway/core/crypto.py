"""Symmetric encryption for secrets stored in the managed_* tables.

Uses Fernet (AES-128-CBC with HMAC-SHA256 authentication). The key is
read from VOICEGW_SECRET env var or auto-generated and persisted to
``~/.config/voicegateway/.secret`` with chmod 600 on first run.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_SECRET_FILE = Path.home() / ".config" / "voicegateway" / ".secret"

_fernet: Fernet | None = None


def get_secret() -> bytes:
    """Return the Fernet key, from env or secret file.

    Priority: VOICEGW_SECRET env > ~/.config/voicegateway/.secret file.
    If neither exists, generate and persist a new key with chmod 600.
    """
    env_secret = os.environ.get("VOICEGW_SECRET")
    if env_secret:
        return env_secret.encode()

    if _SECRET_FILE.exists():
        _SECRET_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)  # enforce 0600
        return _SECRET_FILE.read_bytes().strip()

    # First run — generate and persist atomically
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


def _get_fernet() -> Fernet:
    global _fernet  # noqa: PLW0603
    if _fernet is None:
        _fernet = Fernet(get_secret())
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
    """Decrypt a string. Empty input returns empty string.

    Raises ValueError if the ciphertext is invalid (typically means the
    secret key changed since encryption).
    """
    if not ciphertext:
        return ""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise ValueError(
            "Failed to decrypt managed credential. "
            "This typically means VOICEGW_SECRET changed since the value was stored. "
            "Re-add the affected providers via the dashboard or MCP."
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
