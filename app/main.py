"""Main FastAPI application entrypoint."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import (
    feeds_router,
    folders_router,
    gateway_router,
    routes_router,
    settings_router,
    status_router,
)
from app.api.auth import AdminMiddleware
from app.api.auth import router as auth_router
from app.core.auth import admin_token
from app.core.bootstrap import seed_default_feed
from app.core.config import settings
from app.core.runtime import get_engine
from app.core.service_errors import ServiceError
from app.core.version import get_version_string
from app.db.session import init_db
from app.drivers import get_driver

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup and shutdown lifecycle management."""

    # 0. Configure logging level
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("app").setLevel(log_level)

    # 1. Initialize SQLite database and WAL mode (also reloads persisted settings)
    admin_token()
    await init_db()
    await seed_default_feed()

    # 2. Initialize router driver (settings were already reloaded by init_db)
    driver = get_driver()
    await driver.initialize()

    engine = get_engine()
    engine.start_periodic_sync()
    try:
        yield
    finally:
        await engine.stop()
        await get_driver().close()


app = FastAPI(
    title="TeByPass",
    version=get_version_string(),
    description="Agnostic, modular L3 split-routing bypass engine for arbitrary network endpoints",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.add_middleware(AdminMiddleware)
app.include_router(auth_router)


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


# Register API Routers
app.include_router(status_router)
app.include_router(routes_router)
app.include_router(feeds_router)
app.include_router(folders_router)
app.include_router(settings_router)
app.include_router(gateway_router)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_view(request: Request) -> Any:
    """Render the industrial web dashboard interface."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "driver_name": settings.router_driver,
            "version": get_version_string(),
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
