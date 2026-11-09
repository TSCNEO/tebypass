"""Local administrator credential and expiring, signed browser sessions."""

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.crypto import _resolve_data_dir

router = APIRouter(tags=["Authentication"])
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
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
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
