"""Cryptographic utilities for encrypting sensitive settings at rest in SQLite.

All secrets (UniFi API keys, router passwords, etc.) are encrypted before being
written to SQLite using authenticated symmetric encryption (Fernet / AES-128-CBC + HMAC-SHA256).
The encryption key is stored in /data/.secret_key (chmod 600) or derived from
TEBYPASS_SECRET_KEY.

Secrets stored prior to encryption (legacy plaintext) are transparently decrypted
and automatically re-encrypted on next load or save.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from pathlib import Path
from typing import Final

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("tebypass.crypto")

CIPHERTEXT_PREFIX: Final[str] = "enc:v1:"
_CACHED_FERNET: Fernet | None = None
_CACHED_KEY: bytes | None = None


def derive_key(seed: str) -> bytes:
    """Derive a URL-safe 32-byte Fernet key deterministically from any seed string."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _resolve_data_dir() -> Path:
    """Extract the directory where the SQLite database lives."""
    from app.core.config import settings

    db_url = (
        os.environ.get("TEBYPASS_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or settings.database_url
        or ""
    )
    if db_url and "sqlite" in db_url:
        if db_url.startswith("sqlite+aiosqlite:////") or db_url.startswith("sqlite:////"):
            path_part = "/" + db_url.split(":////", 1)[-1]
            return Path(path_part).parent
        if ":///" in db_url:
            path_part = db_url.split(":///", 1)[-1]
            return Path(path_part).parent
    db_path = os.environ.get("TEBYPASS_DB_PATH", settings.db_path)
    return Path(db_path).parent


def get_encryption_key() -> bytes:
    """Retrieve or generate the Fernet key used for encrypting settings at rest."""
    global _CACHED_KEY, _CACHED_FERNET
    if _CACHED_KEY is not None:
        return _CACHED_KEY

    # 1. Explicit environment variable override
    env_secret = os.environ.get("TEBYPASS_SECRET_KEY", "").strip()
    if env_secret and env_secret != "change-me-in-production-neutral-secret-key":
        key = derive_key(env_secret)
        _CACHED_KEY = key
        _CACHED_FERNET = Fernet(key)
        return key

    # 2. Persisted key file in the data volume
    data_dir = _resolve_data_dir()
    key_file = data_dir / ".secret_key"
    if key_file.exists():
        try:
            content = key_file.read_bytes().strip()
            Fernet(content)
        except Exception as ex:
            raise RuntimeError("Existing encryption key is invalid; restore it from backup") from ex
        _CACHED_KEY = content
        _CACHED_FERNET = Fernet(content)
        return content

    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        new_key = Fernet.generate_key()
        try:
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(new_key)
        except FileExistsError:
            new_key = key_file.read_bytes().strip()
        _CACHED_FERNET = Fernet(new_key)
        _CACHED_KEY = new_key
        return new_key
    except Exception as ex:
        raise RuntimeError("Cannot persist encryption key; refusing insecure fallback") from ex


def get_fernet() -> Fernet:
    """Return the active Fernet cipher instance."""
    global _CACHED_FERNET
    if _CACHED_FERNET is None:
        get_encryption_key()
    assert _CACHED_FERNET is not None
    return _CACHED_FERNET


def reset_cached_key() -> None:
    """Reset cached Fernet key (used by tests)."""
    global _CACHED_KEY, _CACHED_FERNET
    _CACHED_KEY = None
    _CACHED_FERNET = None


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a plaintext secret string and return the prefixed ciphertext.

    Empty strings are returned as-is. Already encrypted values are not double-encrypted.
    """
    if not plaintext:
        return ""
    if plaintext.startswith(CIPHERTEXT_PREFIX):
        return plaintext
    fernet = get_fernet()
    token = fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")
    return f"{CIPHERTEXT_PREFIX}{token}"


def decrypt_secret(value: str) -> tuple[str, bool]:
    """Decrypt a secret value.

    Returns:
        tuple[str, bool]: (decrypted_plaintext, was_encrypted)
        If the value is not prefixed with ``enc:v1:``, it is returned as plaintext
        with ``was_encrypted=False`` for backward compatibility.
    """
    if not value:
        return "", False
    if not value.startswith(CIPHERTEXT_PREFIX):
        return value, False

    token = value[len(CIPHERTEXT_PREFIX) :].encode("utf-8")
    fernet = get_fernet()
    try:
        decrypted = fernet.decrypt(token).decode("utf-8")
        return decrypted, True
    except InvalidToken as ex:
        logger.error("Failed to decrypt stored secret: invalid token or encryption key changed")
        raise ValueError("Cannot decrypt stored secret with current encryption key") from ex


def is_encrypted(value: str) -> bool:
    """Check if a string represents an encrypted secret value."""
    return bool(value and value.startswith(CIPHERTEXT_PREFIX))
