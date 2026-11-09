"""System health and status endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.version import get_version_info, get_version_string
from app.db.models import ResolvedIP, Target
from app.db.session import get_db
from app.drivers import get_driver
from app.models.schemas import DriverStatusResponse, SystemStatusResponse

router = APIRouter(tags=["Status"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Basic container healthcheck endpoint."""
    return {
        "status": "ok",
        "timestamp": datetime.now(UTC).isoformat(),
        "service": "tebypass",
    }


@router.get("/api/v1/status", response_model=SystemStatusResponse)
async def get_system_status(db: AsyncSession = Depends(get_db)) -> SystemStatusResponse:
    """Detailed telemetry and router driver health status."""
    driver = get_driver()
    driver_status = await driver.health_check()

    # Query counts from SQLite WAL
    targets_count_res = await db.execute(select(func.count(Target.id)))
    total_targets = targets_count_res.scalar() or 0

    resolved_count_res = await db.execute(select(func.count(ResolvedIP.id)))
    total_resolved = resolved_count_res.scalar() or 0

    driver_resp = DriverStatusResponse(
        driver_name=driver_status.driver_name,
        health=driver_status.health.value,
        active_routes_count=driver_status.active_routes_count,
        last_sync=driver_status.last_sync,
        details=driver_status.details,
    )

    return SystemStatusResponse(
        status="healthy" if driver_status.health.value != "unhealthy" else "degraded",
        version=get_version_string(),
        driver=driver_resp,
        total_targets=total_targets,
        total_resolved_ips=total_resolved,
        active_routes=driver_status.active_routes_count,
        sliding_window_seconds=settings.sliding_window_seconds,
    )


@router.get("/api/v1/version")
async def get_version_endpoint() -> dict[str, str | int]:
    """Return detailed dynamic version and build metadata."""
    return get_version_info()
