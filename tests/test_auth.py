"""Authentication, session expiry and cross-origin writes."""

from typing import cast

import httpx

from app.core.auth import admin_token, session_valid
from app.main import app


async def test_anonymous_cannot_read_or_change_settings(db_ready: None) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://testserver"
    ) as client:
        assert (await client.get("/api/v1/settings")).status_code == 401
        assert (
            await client.put("/api/v1/settings", json={"routing_enabled": False})
        ).status_code == 401
        assert (await client.get("/healthz")).status_code == 200
        response = await client.post("/auth/session", json={"token": admin_token()})
        assert response.status_code == 200
        assert "HttpOnly" in response.headers["set-cookie"]
        assert (await client.get("/api/v1/settings")).status_code == 200
        assert (
            await client.put(
                "/api/v1/settings", json={}, headers={"Origin": "https://other.example.org"}
            )
        ).status_code == 403
        await client.delete("/auth/session")
        assert (await client.get("/api/v1/settings")).status_code == 401


def test_invalid_sessions_are_rejected() -> None:
    assert not session_valid("invalid")
    assert not session_valid("0.invalid")


async def test_tls_proxy_accepts_same_origin_and_sets_secure_cookie(db_ready: None) -> None:
    from starlette.types import ASGIApp, Receive, Scope, Send
    from uvicorn._types import ASGI3Application
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    proxy_app = cast(
        ASGIApp, ProxyHeadersMiddleware(cast(ASGI3Application, app), trusted_hosts=["192.0.2.10"])
    )

    async def internal_http(scope: Scope, receive: Receive, send: Send) -> None:
        scope["scheme"] = "http"
        await proxy_app(scope, receive, send)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(internal_http, client=("192.0.2.10", 1234)),
        base_url="https://tebypass.example.org",
        headers={"X-Forwarded-Proto": "https"},
    ) as client:
        response = await client.post("/auth/session", json={"token": admin_token()})
        assert response.status_code == 200
        assert "Secure" in response.headers["set-cookie"]
        for origin in ("https://tebypass.example.org", "https://tebypass.example.org:443"):
            response = await client.put("/api/v1/settings", json={}, headers={"Origin": origin})
            assert response.status_code == 200
        for invalid_origin in (
            "https://other.example.org",
            "https://tebypass.example.org:0",
            "https://tebypass.example.org:8443",
            "null",
            "https://user@tebypass.example.org",
            "https://tebypass.example.org/path",
        ):
            response = await client.put(
                "/api/v1/settings", json={}, headers={"Origin": invalid_origin}
            )
            assert response.status_code == 403
        response = await client.put(
            "/api/v1/settings",
            json={},
            headers={
                "Origin": "https://other.example.org",
                "X-Forwarded-Host": "other.example.org",
            },
        )
        assert response.status_code == 403


async def test_untrusted_peer_cannot_spoof_proxy_scheme(db_ready: None) -> None:
    from starlette.types import ASGIApp, Receive, Scope, Send
    from uvicorn._types import ASGI3Application
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    proxy_app = cast(
        ASGIApp, ProxyHeadersMiddleware(cast(ASGI3Application, app), trusted_hosts=["192.0.2.10"])
    )

    async def internal_http(scope: Scope, receive: Receive, send: Send) -> None:
        scope["scheme"] = "http"
        await proxy_app(scope, receive, send)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(internal_http, client=("198.51.100.10", 1234)),
        base_url="https://tebypass.example.org",
        headers={"X-Forwarded-Proto": "https"},
    ) as client:
        await client.post("/auth/session", json={"token": admin_token()})
        response = await client.put(
            "/api/v1/settings", json={}, headers={"Origin": "https://tebypass.example.org"}
        )
        assert response.status_code == 403
