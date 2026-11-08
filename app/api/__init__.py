"""API routers package."""

from app.api.feeds import router as feeds_router
from app.api.routes import router as routes_router
from app.api.status import router as status_router

__all__ = ["feeds_router", "routes_router", "status_router"]
