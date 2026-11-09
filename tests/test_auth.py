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
        assert (await client.get("/healthz")).json()["scheme"] == "https"
        for origin in ("https://tebypass.example.org", "https://tebypass.example.org:443"):
            response = await client.put("/api/v1/settings", json={}, headers={"Origin": origin})
            assert response.status_code == 200
        for invalid_origin in (
            "https://other.example.org",
            "https://tebypass.example.org:0",
            "https://tebypass.example.org:8443",
            "null",
            "https://[invalid",
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


async def test_proxy_mismatch_gives_actionable_guidance(db_ready: None) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://tebypass.example.org"
    ) as client:
        await client.post("/auth/session", json={"token": admin_token()})
        assert (await client.get("/healthz")).json()["scheme"] == "http"
        response = await client.put(
            "/api/v1/settings", json={}, headers={"Origin": "https://tebypass.example.org"}
        )
        assert response.status_code == 403
        assert response.json()["code"] == "proxy_setup_required"
        response = await client.put(
            "/api/v1/settings", json={}, headers={"Origin": "https://[invalid"}
        )
        assert response.status_code == 403


async def test_browser_origin_works_without_proxy_trust(db_ready: None) -> None:
    from starlette.types import Receive, Scope, Send

    async def internal_http(scope: Scope, receive: Receive, send: Send) -> None:
        scope["scheme"] = "http"
        await app(scope, receive, send)

    origin = "https://tebypass.example.org:8443"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(internal_http), base_url=origin
    ) as client:
        response = await client.post(
            "/auth/session",
            json={"token": admin_token(), "browser_origin": origin},
            headers={"Origin": origin, "X-Forwarded-Proto": "http"},
        )
        assert response.status_code == 200
        assert "Secure" in response.headers["set-cookie"]
        assert "HttpOnly" in response.headers["set-cookie"]
        assert (await client.get("/api/v1/settings")).status_code == 200
        response = await client.put("/api/v1/settings", json={}, headers={"Origin": origin})
        assert response.status_code == 200
        for invalid in (
            "https://other.example.org:8443",
            "https://tebypass.example.org",
            "http://tebypass.example.org:8443",
            "null",
            "https://[invalid",
        ):
            response = await client.put("/api/v1/settings", json={}, headers={"Origin": invalid})
            assert response.status_code == 403
        assert (await client.put("/api/v1/settings", json={})).status_code == 403
        assert (await client.delete("/auth/session", headers={"Origin": origin})).status_code == 200
        assert (await client.get("/api/v1/settings")).status_code == 401


async def test_browser_login_rejects_unproven_origin(db_ready: None) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://tebypass.example.org"
    ) as client:
        for claimed, header in (
            ("https://other.example.org", "https://other.example.org"),
            ("https://tebypass.example.org", "https://other.example.org"),
            ("https://tebypass.example.org", ""),
            ("https://tebypass.example.org:8443", "https://tebypass.example.org:8443"),
            ("https://tebypass.example.org/path", "https://tebypass.example.org/path"),
        ):
            response = await client.post(
                "/auth/session",
                json={"token": admin_token(), "browser_origin": claimed},
                headers={"Origin": header, "X-Forwarded-Host": "other.example.org"},
            )
            assert response.status_code == 403
            assert "set-cookie" not in response.headers
        response = await client.post(
            "/auth/session",
            json={
                "token": "invalid-token-with-enough-characters",
                "browser_origin": "http://tebypass.example.org",
            },
            headers={"Origin": "http://tebypass.example.org"},
        )
        assert response.status_code == 401
        response = await client.post(
            "/auth/session",
            json={"token": admin_token(), "browser_origin": "http://tebypass.example.org"},
            headers={"Origin": "http://tebypass.example.org"},
        )
        assert response.status_code == 200
        assert "Secure" not in response.headers["set-cookie"]
        assert (
            await client.put(
                "/api/v1/settings", json={}, headers={"Origin": "http://tebypass.example.org"}
            )
        ).status_code == 200


def test_origin_bound_session_tampering_and_expiry() -> None:
    from unittest.mock import patch

    from app.core.auth import issue_session, session_origin

    session = issue_session("https://tebypass.example.org")
    assert session_valid(session)
    assert session_origin(session) == "https://tebypass.example.org"
    assert not session_valid(session + "tampered")
    assert session_origin(session + "tampered") is None
    assert session_origin(issue_session()) is None
    with patch("app.core.auth.time.time", return_value=0):
        expired = issue_session("https://tebypass.example.org")
    assert not session_valid(expired)
    assert session_origin(expired) is None


async def test_browser_sessions_allow_independent_entry_points(db_ready: None) -> None:
    from starlette.types import Receive, Scope, Send

    async def internal_http(scope: Scope, receive: Receive, send: Send) -> None:
        scope["scheme"] = "http"
        await app(scope, receive, send)

    for origin in ("https://tebypass.example.org", "http://192.0.2.20:9384"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(internal_http), base_url=origin
        ) as client:
            response = await client.post(
                "/auth/session",
                json={"token": admin_token(), "browser_origin": origin},
                headers={"Origin": origin},
            )
            assert response.status_code == 200
            response = await client.put("/api/v1/settings", json={}, headers={"Origin": origin})
            assert response.status_code == 200


def test_origin_normalization_handles_ipv6_and_default_ports() -> None:
    from app.api.origins import normalized_origin

    assert normalized_origin("https://tebypass.example.org:443") == "https://tebypass.example.org"
    assert normalized_origin("http://[2001:db8::1]:9384") == "http://[2001:db8::1]:9384"
    assert normalized_origin("https://tebypass.example.org:0") == "https://tebypass.example.org:0"
    for invalid in (
        "null",
        "https://[invalid",
        "https://user@tebypass.example.org",
        "https://tebypass.example.org:bad",
    ):
        assert normalized_origin(invalid) is None
