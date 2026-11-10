"""Bounded feed downloads; no implicit JSON-to-HTML fallback or redirects."""

from urllib.parse import urlsplit

import httpx

MAX_FEED_BYTES = 16 * 1024 * 1024


def validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Feed URL must use HTTP(S) and include a hostname")
    if parsed.username or parsed.password:
        raise ValueError("Feed URLs cannot contain credentials")


async def download(url: str, etag: str | None = None) -> tuple[str | None, str | None]:
    validate_url(url)
    headers = {"User-Agent": "TeByPass/0.2.1"}
    if etag:
        headers["If-None-Match"] = etag
    async with (
        httpx.AsyncClient(timeout=30) as client,
        client.stream("GET", url, headers=headers) as response,
    ):
        if response.status_code == 304:
            return None, etag
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("Feed must return HTTP 200 or 304")
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > MAX_FEED_BYTES:
                raise ValueError("Feed exceeds maximum response size")
        return content.decode("utf-8-sig"), response.headers.get("etag")
