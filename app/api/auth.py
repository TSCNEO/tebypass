"""Authentication HTTP boundary and administrator middleware."""

import hmac
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.api.origins import browser_login_origin, normalized_origin, same_origin
from app.core.auth import (
    COOKIE,
    SESSION_SECONDS,
    admin_token,
    issue_session,
    session_origin,
    session_valid,
)

router = APIRouter(tags=["Authentication"])


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
            bound_origin = session_origin(request.cookies.get(COOKIE, ""))
            if bound_origin is not None:
                if normalized_origin(origin or "") != bound_origin:
                    return JSONResponse(
                        {"detail": "Cross-origin request rejected"}, status_code=403
                    )
            elif origin and not same_origin(origin, request):
                try:
                    parsed = urlsplit(origin)
                except ValueError:
                    return JSONResponse(
                        {"detail": "Cross-origin request rejected"}, status_code=403
                    )
                if (
                    parsed.scheme == "https"
                    and parsed.hostname == request.url.hostname
                    and request.url.scheme == "http"
                ):
                    return JSONResponse(
                        {
                            "code": "proxy_setup_required",
                            "detail": "Vuelve a iniciar sesión para reconocer automáticamente la dirección del panel.",
                        },
                        status_code=403,
                    )
                return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


class LoginRequest(BaseModel):
    token: str = Field(min_length=16, max_length=512)
    browser_origin: str | None = Field(default=None, max_length=2048)


@router.post("/auth/session")
async def login(payload: LoginRequest, request: Request) -> Response:
    if not hmac.compare_digest(payload.token, admin_token()):
        raise HTTPException(status_code=401, detail="Invalid administrator credential")
    origin = None
    if payload.browser_origin is not None:
        origin = browser_login_origin(payload.browser_origin, request)
        if origin is None:
            raise HTTPException(
                status_code=403,
                detail="El origen del navegador no coincide con el acceso al panel.",
            )
    response = JSONResponse({"success": True})
    response.set_cookie(
        COOKIE,
        issue_session(origin),
        httponly=True,
        samesite="strict",
        secure=(origin or str(request.url)).startswith("https://"),
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
