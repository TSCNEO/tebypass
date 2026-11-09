"""Target management and route synchronization endpoints."""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.cache import SlidingWindowCache
from app.core.config import settings
from app.core.feeds import FeedManager
from app.core.resolver import NativeDNSResolver
from app.core.service_errors import ServiceError
from app.core.targets import as_utc, dedupe_key, detect_destination_type
from app.db.models import Folder, ResolvedIP, Target
from app.drivers import get_driver
from app.models.schemas import (
    BulkTargetImportRequest,
    BulkTargetImportResponse,
    SyncResponse,
    SyncTriggerRequest,
    TargetCreate,
    TargetResolveResponse,
    TargetResponse,
    TargetUpdate,
)

logger = logging.getLogger(__name__)

_TARGET_LOAD_OPTIONS = (
    selectinload(Target.resolved_ips),
    selectinload(Target.folder),
)


async def _resolve_and_persist(db: AsyncSession, target: Target) -> list[str]:
    """Resolve an FQDN target immediately and persist ResolvedIP records in SQLite."""
    if target.destination_type != "fqdn":
        return []
    cache = SlidingWindowCache(default_window_seconds=settings.sliding_window_seconds)
    resolver = NativeDNSResolver(cache=cache)
    try:
        resolutions = await resolver.resolve_fqdn(
            target.value,
            window_extension_seconds=settings.sliding_window_seconds,
        )
    except Exception as ex:
        logger.warning("Immediate resolution failed for '%s': %s", target.value, ex)
        return []

    now = datetime.now(UTC)
    for resolution in resolutions:
        lookup = select(ResolvedIP).where(
            ResolvedIP.target_id == target.id,
            ResolvedIP.ip_address == resolution.ip_address,
        )
        stored = (await db.execute(lookup)).scalar_one_or_none()
        if stored is None:
            db.add(
                ResolvedIP(
                    target_id=target.id,
                    ip_address=resolution.ip_address,
                    cidr=resolution.cidr,
                    first_seen_at=now,
                    last_seen_at=now,
                    expires_at=resolution.expires_at,
                    ttl_seconds=resolution.dns_ttl,
                )
            )
        else:
            stored.last_seen_at = now
            stored.expires_at = resolution.expires_at
            stored.ttl_seconds = resolution.dns_ttl

    if resolutions:
        await db.commit()
    return sorted({r.ip_address for r in resolutions})


async def _trigger_background_reconciliation() -> None:
    """Best-effort trigger to immediately push changes to router or gateway."""
    try:
        from app.core.runtime import trigger_reconciliation

        await trigger_reconciliation(force=False)
    except Exception as ex:
        logger.warning("Auto-reconciliation after target change failed: %s", ex)


def _active_ips(target: Target, now: datetime | None = None) -> list[str]:
    """Return non-expired resolved IPs of a target, sorted and deduplicated."""
    current_time = now or datetime.now(UTC)
    return sorted(
        {rip.ip_address for rip in target.resolved_ips if as_utc(rip.expires_at) > current_time}
    )


def _to_response(target: Target, now: datetime | None = None) -> TargetResponse:
    resolved_ips = _active_ips(target, now)
    return TargetResponse(
        id=target.id,
        feed_id=target.feed_id,
        folder_id=target.folder_id,
        folder_name=target.folder.name if target.folder else None,
        value=target.value,
        destination_type="cidr" if target.destination_type == "cidr" else "fqdn",
        description=target.description,
        enabled=target.enabled,
        created_at=target.created_at,
        resolved_count=len(resolved_ips),
        resolved_ips=resolved_ips,
    )


async def _load_target(db: AsyncSession, target_id: str) -> Target:
    query = select(Target).where(Target.id == target_id).options(*_TARGET_LOAD_OPTIONS)
    target = (await db.execute(query)).scalar_one_or_none()
    if target is None:
        raise ServiceError(status_code=404, detail="Target not found")
    return target


async def _validate_folder(db: AsyncSession, folder_id: str) -> None:
    folder = await db.get(Folder, folder_id)
    if folder is None:
        raise ServiceError(status_code=404, detail="Folder not found")


async def list_targets(db: AsyncSession) -> list[TargetResponse]:
    """Retrieve all configured targets with their currently resolved IPs."""
    query = select(Target).options(*_TARGET_LOAD_OPTIONS).order_by(Target.created_at.desc())
    result = await db.execute(query)
    now = datetime.now(UTC)
    return [_to_response(target, now) for target in result.scalars().all()]


async def create_target(
    payload: TargetCreate,
    db: AsyncSession,
) -> TargetResponse:
    """Create a new manual target (FQDN or CIDR)."""
    raw_val = payload.value.strip()
    if not raw_val:
        raise ServiceError(status_code=400, detail="Target value must not be empty")

    if payload.folder_id:
        await _validate_folder(db, payload.folder_id)

    dest_type = payload.destination_type or detect_destination_type(raw_val)

    target = Target(
        value=raw_val,
        destination_type=dest_type,
        description=payload.description,
        enabled=payload.enabled,
        folder_id=payload.folder_id,
    )
    db.add(target)
    await db.commit()

    if target.enabled and target.destination_type == "fqdn":
        await _resolve_and_persist(db, target)
    await _trigger_background_reconciliation()

    loaded = await _load_target(db, target.id)
    return _to_response(loaded)


async def update_target(
    target_id: str,
    payload: TargetUpdate,
    db: AsyncSession,
) -> TargetResponse:
    """Partially update a target (value, description, enabled flag or folder)."""
    target = await _load_target(db, target_id)
    changes = payload.model_dump(exclude_unset=True)

    if "value" in changes:
        raw_val = (changes["value"] or "").strip()
        if not raw_val:
            raise ServiceError(status_code=400, detail="Target value must not be empty")
        from sqlalchemy import delete

        await db.execute(delete(ResolvedIP).where(ResolvedIP.target_id == target.id))
        parsed = FeedManager().parse_entry(raw_val)
        if parsed is None:
            raise ServiceError(status_code=400, detail="Invalid destination")
        target.value = parsed.value
        target.destination_type = parsed.destination_type

    if "description" in changes:
        target.description = changes["description"]

    if "enabled" in changes and changes["enabled"] is not None:
        target.enabled = bool(changes["enabled"])

    if "folder_id" in changes:
        folder_id = changes["folder_id"]
        if folder_id:
            await _validate_folder(db, folder_id)
        target.folder_id = folder_id

    await db.commit()

    if target.enabled and target.destination_type == "fqdn":
        await _resolve_and_persist(db, target)
    await _trigger_background_reconciliation()

    loaded = await _load_target(db, target_id)
    return _to_response(loaded)


async def delete_target(
    target_id: str,
    db: AsyncSession,
) -> None:
    """Delete a target by ID."""
    query = select(Target).where(Target.id == target_id)
    target = (await db.execute(query)).scalar_one_or_none()
    if not target:
        raise ServiceError(status_code=404, detail="Target not found")

    await db.delete(target)
    await db.commit()
    await _trigger_background_reconciliation()


async def bulk_import_targets(
    payload: BulkTargetImportRequest,
    db: AsyncSession,
) -> BulkTargetImportResponse:
    """Import targets from a list of raw lines, skipping invalid and duplicate entries."""
    if payload.folder_id:
        await _validate_folder(db, payload.folder_id)

    manager = FeedManager()
    existing_query = select(Target.value)
    existing_keys = {
        dedupe_key(value) for value in (await db.execute(existing_query)).scalars().all()
    }

    created = 0
    skipped = 0
    invalid: list[str] = []

    for chunk in payload.entries:
        for raw_line in chunk.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parsed = manager.parse_entry(line)
            if parsed is None:
                invalid.append(line)
                continue

            key = dedupe_key(parsed.value)
            if key in existing_keys:
                skipped += 1
                continue

            description = payload.description or parsed.description or None
            db.add(
                Target(
                    value=parsed.value,
                    destination_type=parsed.destination_type,
                    description=description,
                    enabled=True,
                    folder_id=payload.folder_id,
                )
            )
            existing_keys.add(key)
            created += 1

    if created:
        await db.commit()
        # Resolve any newly imported enabled FQDN targets immediately
        fqdn_query = select(Target).where(
            Target.enabled == True,  # noqa: E712
            Target.destination_type == "fqdn",
        )
        for t in (await db.execute(fqdn_query)).scalars().all():
            await _resolve_and_persist(db, t)
        await _trigger_background_reconciliation()

    return BulkTargetImportResponse(created=created, skipped=skipped, invalid=invalid)


async def resolve_target_now(
    target_id: str,
    db: AsyncSession,
) -> TargetResolveResponse:
    """Force an immediate DNS resolution for one target and persist the result."""
    target = await _load_target(db, target_id)

    if target.destination_type != "fqdn":
        return TargetResolveResponse(value=target.value, ips=[target.value])

    ips = await _resolve_and_persist(db, target)
    await _trigger_background_reconciliation()

    return TargetResolveResponse(
        value=target.value,
        ips=ips,
    )


async def get_active_routes() -> list[str]:
    """List all CIDRs currently injected in the active router driver."""
    driver = get_driver()
    routes = await driver.get_current_routes()
    return sorted(routes)


async def trigger_sync(
    payload: SyncTriggerRequest | None = None,
) -> SyncResponse:
    """Manually trigger route reconciliation with router driver."""
    from app.core.runtime import trigger_reconciliation

    force = payload.force if payload else False
    result = await trigger_reconciliation(force=force)

    return SyncResponse(
        driver_name=result.driver_name,
        added_count=result.added_count,
        removed_count=result.removed_count,
        total_active=result.total_active,
        duration_ms=result.duration_ms,
        success=result.success,
        error_message=result.error_message,
    )
