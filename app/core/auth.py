"""Local administrator credential and expiring, signed browser sessions."""

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from app.core.crypto import _resolve_data_dir

COOKIE = "tebypass_session"
SESSION_SECONDS = 12 * 3600


def token_path() -> Path:
    return _resolve_data_dir() / "admin_token"


def admin_token() -> str:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(secrets.token_urlsafe(32) + "\n")
        except FileExistsError:
            pass
    token = path.read_text().strip()
    if len(token) < 32:
        raise RuntimeError("Invalid administrator token file")
    return token


def sign(value: str) -> str:
    return hmac.new(admin_token().encode(), value.encode(), hashlib.sha256).hexdigest()


def session_valid(value: str) -> bool:
    try:
        stamp, signature = value.split(".", 1)
        age = time.time() - int(stamp)
        return 0 <= age < SESSION_SECONDS and hmac.compare_digest(sign(stamp), signature)
    except (ValueError, TypeError):
        return False
