"""Main FastAPI application entrypoint."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.api import (
    feeds_router,
    folders_router,
    gateway_router,
    routes_router,
    settings_router,
    status_router,
)
from app.core.bootstrap import seed_default_feed
from app.core.cache import SlidingWindowCache
from app.core.config import settings
from app.core.engine import ReconciliationEngine
from app.core.resolver import NativeDNSResolver
from app.core.version import get_version_string
from app.db.session import init_db
from app.drivers import get_driver

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

engine_instance: ReconciliationEngine | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup and shutdown lifecycle management."""
    global engine_instance

    # 0. Configure logging level
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("app").setLevel(log_level)

    # 1. Initialize SQLite database and WAL mode (also reloads persisted settings)
    await init_db()
    await seed_default_feed()

    # 2. Initialize router driver (settings were already reloaded by init_db)
    driver = get_driver()
    await driver.initialize()

    # 3. Setup resolver and sliding window cache
    cache = SlidingWindowCache(default_window_seconds=settings.sliding_window_seconds)
    resolver = NativeDNSResolver(cache=cache)

    # 4. Instantiate and start core reconciliation engine
    engine_instance = ReconciliationEngine(
        driver=driver,
        cache=cache,
        resolver=resolver,
        sync_interval_seconds=settings.sync_interval_seconds,
    )

    if settings.sync_interval_seconds > 0:
        engine_instance.start_periodic_sync()

    yield

    # Shutdown procedure
    if engine_instance:
        await engine_instance.stop()
    await driver.close()


app = FastAPI(
    title="TeByPass",
    version=get_version_string(),
    description="Agnostic, modular L3 split-routing bypass engine for arbitrary network endpoints",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
