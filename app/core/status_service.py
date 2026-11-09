"""System health and status endpoints."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.version import get_version_info, get_version_string
from app.db.models import Feed, ResolvedIP, SyncEvent, Target
from app.drivers import get_driver
from app.models.schemas import DriverStatusResponse, SystemStatusResponse


async def healthz() -> dict[str, str]:
    """Basic container healthcheck endpoint."""
    return {
        "status": "ok",
        "timestamp": datetime.now(UTC).isoformat(),
        "service": "tebypass",
    }


async def get_system_status(db: AsyncSession) -> SystemStatusResponse:
    """Detailed telemetry and router driver health status."""
    driver = get_driver()
    driver_status = driver.get_status()

    # Query counts from SQLite WAL
    targets_count_res = await db.execute(select(func.count(Target.id)))
    total_targets = targets_count_res.scalar() or 0

    resolved_count_res = await db.execute(
        select(func.count(ResolvedIP.id)).where(ResolvedIP.expires_at > datetime.now(UTC))
    )
    total_resolved = resolved_count_res.scalar() or 0

    driver_resp = DriverStatusResponse(
        driver_name=driver_status.driver_name,
        health=driver_status.health.value,
        active_routes_count=driver_status.active_routes_count,
        last_sync=driver_status.last_sync,
        details=driver_status.details,
    )

    latest = (
        await db.execute(select(SyncEvent).order_by(SyncEvent.executed_at.desc()).limit(1))
    ).scalar_one_or_none()
    feed_errors = (
        await db.execute(
            select(func.count(Feed.id)).where(Feed.enabled.is_(True), Feed.last_error.is_not(None))
        )
    ).scalar() or 0
    healthy = (
        driver_status.health.value == "healthy"
        and not feed_errors
        and (latest is None or latest.success)
    )
    return SystemStatusResponse(
        status="healthy" if healthy else "degraded",
        version=get_version_string(),
        driver=driver_resp,
        total_targets=total_targets,
        total_resolved_ips=total_resolved,
        active_routes=driver_status.active_routes_count,
        sliding_window_seconds=settings.sliding_window_seconds,
        routing_enabled=settings.routing_enabled,
        last_sync_success=latest.success if latest else None,
        last_sync_error=latest.error_message if latest else None,
        last_attempt_at=latest.executed_at if latest else None,
        feed_errors=feed_errors,
    )


async def get_version_endpoint() -> dict[str, str | int]:
    """Return detailed dynamic version and build metadata."""
    return get_version_info()
