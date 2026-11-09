"""Local administrator credential and expiring, signed browser sessions."""

import base64
import binascii
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


def issue_session(origin: str | None = None) -> str:
    stamp = str(int(time.time()))
    value = stamp
    if origin is not None:
        encoded = base64.urlsafe_b64encode(origin.encode()).decode().rstrip("=")
        value = "v2." + stamp + "." + encoded
    return value + "." + sign(value)


def session_origin(value: str) -> str | None:
    """Read only verified, origin-bound sessions; legacy sessions have no origin."""
    if not session_valid(value) or not value.startswith("v2."):
        return None
    encoded = value.split(".")[2]
    try:
        return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None


def session_valid(value: str) -> bool:
    try:
        payload, signature = value.rsplit(".", 1)
        parts = payload.split(".")
        if len(parts) == 1:
            stamp = parts[0]
        elif len(parts) == 3 and parts[0] == "v2":
            stamp = parts[1]
        else:
            return False
        age = time.time() - int(stamp)
        return 0 <= age < SESSION_SECONDS and hmac.compare_digest(sign(payload), signature)
    except (ValueError, TypeError):
        return False
