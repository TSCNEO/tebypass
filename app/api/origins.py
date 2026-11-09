"""Browser origin validation, independent of reverse-proxy forwarding headers."""

from urllib.parse import urlsplit

from fastapi import Request


def normalized_origin(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return None
        port = parsed.port
        hostname = parsed.hostname
        if ":" in hostname:
            hostname = f"[{hostname}]"
        suffix = (
            "" if port is None or port == {"http": 80, "https": 443}[parsed.scheme] else f":{port}"
        )
        return f"{parsed.scheme}://{hostname}{suffix}"
    except ValueError:
        return None


def same_origin(origin: str, request: Request) -> bool:
    expected = normalized_origin(str(request.url.replace(path="", query="", fragment="")))
    return expected is not None and normalized_origin(origin) == expected


def browser_login_origin(value: str, request: Request) -> str | None:
    """Accept an authenticated browser's origin only when Origin and Host corroborate it."""
    origin = normalized_origin(value)
    if origin is None or normalized_origin(request.headers.get("origin", "")) != origin:
        return None
    # Host must be preserved by the reverse proxy; forwarded headers are not evidence.
    host_origin = normalized_origin(
        urlsplit(origin).scheme + "://" + request.headers.get("host", "")
    )
    if host_origin != origin:
        return None
    return origin
