"""Authentication HTTP boundary and administrator middleware."""

import hmac
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.auth import COOKIE, SESSION_SECONDS, admin_token, session_valid, sign

router = APIRouter(tags=["Authentication"])


def same_origin(origin: str, request: Request) -> bool:
    """Compare browser origin to the effective URL set by the trusted proxy layer."""
    try:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return False
        effective = request.url
        defaults = {"http": 80, "https": 443}
        return (
            parsed.scheme,
            parsed.hostname,
            parsed.port if parsed.port is not None else defaults[parsed.scheme],
        ) == (
            effective.scheme,
            effective.hostname,
            effective.port if effective.port is not None else defaults.get(effective.scheme),
        )
    except ValueError:
        return False


class AdminMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in {
            "/healthz",
            "/login",
            "/auth/session",
        } or request.url.path.startswith("/static/"):
            return await call_next(request)
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
        token_ok = bool(bearer) and hmac.compare_digest(bearer, admin_token())
        cookie_ok = session_valid(request.cookies.get(COOKIE, ""))
        if not token_ok and not cookie_ok:
            if request.url.path == "/":
                return Response(status_code=303, headers={"Location": "/login"})
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        if not token_ok and request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and not same_origin(origin, request):
                return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


class LoginRequest(BaseModel):
    token: str = Field(min_length=16, max_length=512)


@router.post("/auth/session")
async def login(payload: LoginRequest, request: Request) -> Response:
    if not hmac.compare_digest(payload.token, admin_token()):
        raise HTTPException(status_code=401, detail="Invalid administrator credential")
    stamp = str(int(time.time()))
    response = JSONResponse({"success": True})
    response.set_cookie(
        COOKIE,
        stamp + "." + sign(stamp),
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=SESSION_SECONDS,
    )
    return response


@router.delete("/auth/session")
async def logout() -> Response:
    response = JSONResponse({"success": True})
    response.delete_cookie(COOKIE)
    return response


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page() -> HTMLResponse:
    return HTMLResponse((Path(__file__).parent.parent / "templates" / "login.html").read_text())
