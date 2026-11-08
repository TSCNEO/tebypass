"""Target management and route synchronization endpoints."""

import ipaddress

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import SlidingWindowCache
from app.core.config import settings
from app.core.engine import ReconciliationEngine
from app.core.resolver import NativeDNSResolver
from app.db.models import Target
from app.db.session import get_db
from app.drivers import get_router_driver
from app.models.schemas import SyncResponse, SyncTriggerRequest, TargetCreate, TargetResponse

router = APIRouter(prefix="/api/v1", tags=["Routes"])


@router.get("/targets", response_model=list[TargetResponse])
async def list_targets(db: AsyncSession = Depends(get_db)) -> list[TargetResponse]:
    """Retrieve all configured targets."""
    query = select(Target).order_by(Target.created_at.desc())
    result = await db.execute(query)
    targets = result.scalars().all()
    responses: list[TargetResponse] = []
    for t in targets:
        responses.append(
            TargetResponse(
                id=t.id,
                feed_id=t.feed_id,
                value=t.value,
                destination_type="cidr" if t.destination_type == "cidr" else "fqdn",
                description=t.description,
                enabled=t.enabled,
                created_at=t.created_at,
                resolved_count=len(t.resolved_ips) if t.resolved_ips else 0,
            )
        )
    return responses


@router.post("/targets", response_model=TargetResponse, status_code=status.HTTP_201_CREATED)
async def create_target(
    payload: TargetCreate,
    db: AsyncSession = Depends(get_db),
) -> TargetResponse:
    """Create a new manual target (FQDN or CIDR)."""
    raw_val = payload.value.strip()

    # Determine destination type
    dest_type = payload.destination_type
    if not dest_type:
        try:
            if "/" in raw_val:
                ipaddress.ip_network(raw_val, strict=False)
            else:
                ipaddress.ip_address(raw_val)
            dest_type = "cidr"
        except ValueError:
            dest_type = "fqdn"

    target = Target(
        value=raw_val,
        destination_type=dest_type,
        description=payload.description,
        enabled=payload.enabled,
    )
    db.add(target)
    await db.commit()
    await db.refresh(target)

    return TargetResponse(
        id=target.id,
        feed_id=target.feed_id,
        value=target.value,
        destination_type=dest_type,
        description=target.description,
        enabled=target.enabled,
        created_at=target.created_at,
        resolved_count=0,
    )


@router.delete("/targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_target(
    target_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a target by ID."""
    query = select(Target).where(Target.id == target_id)
    target = (await db.execute(query)).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    await db.delete(target)
    await db.commit()


@router.get("/routes/active", response_model=list[str])
async def get_active_routes() -> list[str]:
    """List all CIDRs currently injected in the active router driver."""
    driver = get_router_driver()
    routes = await driver.get_current_routes()
    return sorted(routes)


@router.post("/sync", response_model=SyncResponse)
async def trigger_sync(
    payload: SyncTriggerRequest | None = None,
) -> SyncResponse:
    """Manually trigger route reconciliation with router driver."""
    driver = get_router_driver()
    cache = SlidingWindowCache(default_window_seconds=settings.sliding_window_seconds)
    resolver = NativeDNSResolver(cache=cache)
    engine = ReconciliationEngine(driver=driver, cache=cache, resolver=resolver)

    force = payload.force if payload else False
    result = await engine.reconcile(force=force)

    return SyncResponse(
        driver_name=result.driver_name,
        added_count=result.added_count,
        removed_count=result.removed_count,
        total_active=result.total_active,
        duration_ms=result.duration_ms,
        success=result.success,
        error_message=result.error_message,
    )
