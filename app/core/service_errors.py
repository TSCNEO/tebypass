"""Transport-independent service errors, translated by the HTTP boundary."""

import asyncio
import re

import httpx

_URL_SECRET_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_CREDENTIAL_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|password|secret|authorization|cookie)\s*[:=]\s*\S+"
)


def sanitize_driver_error(ex: BaseException, driver_name: str, action: str) -> str:
    """Return an operator-safe error message without URLs, tokens, or raw HTML bodies."""
    if isinstance(ex, TimeoutError | asyncio.TimeoutError | httpx.TimeoutException):
        return f"Timed out while {action} on '{driver_name}'"
    if isinstance(ex, httpx.HTTPStatusError):
        return f"'{driver_name}' returned HTTP {ex.response.status_code} while {action}"
    if isinstance(ex, httpx.ConnectError):
        return f"Could not connect to '{driver_name}' while {action}"
    if isinstance(ex, httpx.HTTPError):
        return f"HTTP transport error ({type(ex).__name__}) while {action} on '{driver_name}'"
    if isinstance(ex, OSError):
        return f"Connection error while {action} on '{driver_name}': {ex.strerror or type(ex).__name__}"
    raw = str(ex).strip().splitlines()[0] if str(ex).strip() else ""
    cleaned = _URL_SECRET_RE.sub("<url>", raw)
    cleaned = _CREDENTIAL_RE.sub(r"\1=<redacted>", cleaned)
    if cleaned and len(cleaned) <= 220:
        return cleaned
    return f"Unexpected error ({type(ex).__name__}) while {action} on '{driver_name}'"


class ServiceError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)
