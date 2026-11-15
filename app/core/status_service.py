"""System health and status endpoints."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.policy_ownership import read_unverified_remote_state
from app.core.targets import as_utc, build_contributing_targets_query
from app.core.version import get_version_info, get_version_string
from app.db.models import Feed, ResolvedIP, SyncEvent, Target
from app.drivers import get_driver
from app.models.schemas import DriverStatusResponse, SystemStatusResponse

_DNS_FAILURE_STATUSES = frozenset({"nxdomain", "timeout", "empty", "error", "budget_exceeded"})


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
    now = datetime.now(UTC)

    # Query counts from SQLite WAL
    targets_count_res = await db.execute(select(func.count(Target.id)))
    total_targets = targets_count_res.scalar() or 0

    resolved_count_res = await db.execute(
        select(func.count(ResolvedIP.id)).where(ResolvedIP.expires_at > now)
    )
    total_resolved = resolved_count_res.scalar() or 0

    # Evaluate DNS coverage only for targets that should actively contribute routes right now.
    contributing_fqdns = list(
        (
            await db.execute(
                build_contributing_targets_query()
                .where(Target.destination_type == "fqdn")
                .options(selectinload(Target.resolved_ips))
            )
        )
        .scalars()
        .all()
    )
    dns_errors = 0
    uncovered_targets = 0
    dns_messages: list[str] = []
    for target in contributing_fqdns:
        active_ips = [rip for rip in target.resolved_ips if as_utc(rip.expires_at) > now]
        has_active_ips = bool(active_ips)
        failed_dns = target.dns_status in _DNS_FAILURE_STATUSES
        expired_coverage = not has_active_ips and (
            target.dns_last_resolved_at is not None or target.dns_last_checked_at is not None
        )
        if failed_dns or expired_coverage:
            dns_errors += 1
        if not has_active_ips and (failed_dns or expired_coverage):
            uncovered_targets += 1
            reason = target.dns_error or "Las IPs retenidas expiraron sin nueva resolución DNS"
            dns_messages.append(f"{target.value}: {reason}")
        elif failed_dns:
            reason = target.dns_error or f"DNS {target.dns_status}"
            dns_messages.append(f"{target.value}: {reason} (usando IPs retenidas)")

    dns_error_detail: str | None = None
    if dns_messages:
        shown = "; ".join(dns_messages[:3])
        if len(dns_messages) > 3:
            shown = f"{shown} (+{len(dns_messages) - 3} más)"
        dns_error_detail = shown

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
    unverified = await read_unverified_remote_state()
    healthy = (
        driver_status.health.value == "healthy"
        and not feed_errors
        and (not settings.routing_enabled or uncovered_targets == 0)
        and unverified is None
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
        dns_errors=dns_errors,
        uncovered_targets=uncovered_targets,
        dns_error_detail=dns_error_detail,
        remote_policy_state=str(unverified["state"]) if unverified else None,
        remote_policy_error=str(unverified.get("detail", "")) if unverified else None,
    )


async def get_version_endpoint() -> dict[str, str | int]:
    """Return detailed dynamic version and build metadata."""
    return get_version_info()
