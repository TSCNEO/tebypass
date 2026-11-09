"""API routers package."""

from app.api.feeds import router as feeds_router
from app.api.folders import router as folders_router
from app.api.gateway import router as gateway_router
from app.api.routes import router as routes_router
from app.api.settings import router as settings_router
from app.api.status import router as status_router

__all__ = [
    "feeds_router",
    "folders_router",
    "gateway_router",
    "routes_router",
    "settings_router",
    "status_router",
]
