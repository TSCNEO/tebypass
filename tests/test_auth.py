"""Authentication, session expiry and cross-origin writes."""

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
