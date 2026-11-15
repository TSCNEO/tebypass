"""Target management and route synchronization endpoints."""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.cache import SlidingWindowCache
from app.core.config import settings
from app.core.feeds import FeedManager
from app.core.resolution_store import upsert_resolved_ips
from app.core.resolver import DNSLookupResult, NativeDNSResolver, classify_dns_exception
from app.core.service_errors import ServiceError, sanitize_driver_error
from app.core.targets import as_utc, dedupe_key, detect_destination_type
from app.db.models import Folder, ResolvedIP, Target
from app.drivers import get_driver
from app.drivers.capabilities import validate_destination
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
    """Resolve an FQDN target immediately and persist ResolvedIP and DNS status in SQLite."""
    if target.destination_type != "fqdn":
        return []
    target_id = target.id
    target_value = target.value
    # Release any read transaction before awaiting DNS so SQLite never upgrades a stale snapshot.
    if db.in_transaction():
        await db.commit()

    cache = SlidingWindowCache(default_window_seconds=settings.sliding_window_seconds)
    resolver = NativeDNSResolver(cache=cache)
    now = datetime.now(UTC)
    outcome: DNSLookupResult

    try:
        outcome = await resolver.resolve_fqdn_with_status(
            target_value,
            window_extension_seconds=settings.sliding_window_seconds,
        )
    except asyncio.CancelledError:
        raise
    except Exception as ex:
        status, detail = classify_dns_exception(ex)
        logger.warning("Immediate resolution failed for '%s': %s", target_value, ex)
        outcome = DNSLookupResult(resolutions=[], status=status, error=detail)

    db_target = await db.get(Target, target_id)
    if db_target is None:
        await db.rollback()
        return []

    db_target.dns_status = outcome.status
    db_target.dns_error = outcome.error
    db_target.dns_last_checked_at = now
    if outcome.resolutions:
        db_target.dns_last_resolved_at = now
        persisted = await upsert_resolved_ips(db, target_id, outcome.resolutions, now)
        if not persisted:
            await db.rollback()
            return []
        await db.commit()
        from app.core.engine import bump_policy_version

        bump_policy_version()
        return sorted({r.ip_address for r in outcome.resolutions})

    await db.commit()
    return []


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
        dns_status=target.dns_status,
        dns_error=target.dns_error,
        dns_last_checked_at=target.dns_last_checked_at,
        dns_last_resolved_at=target.dns_last_resolved_at,
    )


async def _load_target(db: AsyncSession, target_id: str) -> Target:
    query = (
        select(Target)
        .where(Target.id == target_id)
        .options(*_TARGET_LOAD_OPTIONS)
        .execution_options(populate_existing=True)
    )
    target = (await db.execute(query)).scalar_one_or_none()
    if target is None:
        raise ServiceError(status_code=404, detail="Target not found")
    return target


async def _validate_folder(db: AsyncSession, folder_id: str) -> None:
    folder = await db.get(Folder, folder_id)
    if folder is None:
        raise ServiceError(status_code=404, detail="Folder not found")


async def _ensure_manual_target_unique(
    db: AsyncSession,
    canonical_value: str,
    *,
    exclude_target_id: str | None = None,
) -> None:
    query = (
        select(Target)
        .where(Target.feed_id.is_(None), Target.value == canonical_value)
        .options(selectinload(Target.folder))
    )
    if exclude_target_id is not None:
        query = query.where(Target.id != exclude_target_id)
    existing = (await db.execute(query)).scalar_one_or_none()
    if existing is not None:
        location = f" en la carpeta '{existing.folder.name}'" if existing.folder else ""
        raise ServiceError(
            status_code=409,
            detail=f"El destino manual '{canonical_value}' ya existe{location}.",
        )


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

    parsed = FeedManager().parse_entry(raw_val)
    canonical_val = parsed.value if parsed is not None else dedupe_key(raw_val)
    dest_type = (
        payload.destination_type
        or (parsed.destination_type if parsed is not None else None)
        or detect_destination_type(canonical_val)
    )
    try:
        validate_destination(canonical_val, dest_type, settings.router_driver)
    except ValueError as ex:
        raise ServiceError(status_code=400, detail=str(ex)) from ex

    await _ensure_manual_target_unique(db, canonical_val)

    target = Target(
        value=canonical_val,
        destination_type=dest_type,
        description=payload.description,
        enabled=payload.enabled,
        folder_id=payload.folder_id,
    )
    db.add(target)
    try:
        await db.commit()
    except IntegrityError as ex:
        await db.rollback()
        raise ServiceError(
            status_code=409,
            detail=f"El destino manual '{canonical_val}' ya existe.",
        ) from ex

    from app.core.engine import bump_policy_version

    bump_policy_version()

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
    candidate = FeedManager().parse_entry(changes.get("value") or target.value)
    if candidate is not None:
        try:
            validate_destination(
                candidate.value, candidate.destination_type, settings.router_driver
            )
        except ValueError as ex:
            raise ServiceError(status_code=400, detail=str(ex)) from ex

    if "value" in changes:
        raw_val = (changes["value"] or "").strip()
        if not raw_val:
            raise ServiceError(status_code=400, detail="Target value must not be empty")
        parsed = FeedManager().parse_entry(raw_val)
        if parsed is None:
            raise ServiceError(status_code=400, detail="Invalid destination")
        if target.feed_id is None:
            await _ensure_manual_target_unique(db, parsed.value, exclude_target_id=target.id)
        from sqlalchemy import delete

        await db.execute(delete(ResolvedIP).where(ResolvedIP.target_id == target.id))
        target.value = parsed.value
        target.destination_type = parsed.destination_type
        target.dns_status = None
        target.dns_error = None
        target.dns_last_checked_at = None
        target.dns_last_resolved_at = None

    if "description" in changes:
        target.description = changes["description"]

    if "enabled" in changes and changes["enabled"] is not None:
        target.enabled = bool(changes["enabled"])

    if "folder_id" in changes:
        folder_id = changes["folder_id"]
        if folder_id:
            await _validate_folder(db, folder_id)
        target.folder_id = folder_id

    try:
        await db.commit()
    except IntegrityError as ex:
        await db.rollback()
        raise ServiceError(
            status_code=409,
            detail=f"El destino manual '{target.value}' ya existe.",
        ) from ex

    from app.core.engine import bump_policy_version

    bump_policy_version()

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
    from app.core.engine import bump_policy_version

    bump_policy_version()
    await _trigger_background_reconciliation()


async def bulk_import_targets(
    payload: BulkTargetImportRequest,
    db: AsyncSession,
) -> BulkTargetImportResponse:
    """Import targets from a list of raw lines, skipping invalid and duplicate entries."""
    if payload.folder_id:
        await _validate_folder(db, payload.folder_id)

    manager = FeedManager()
    existing_query = select(Target.value).where(Target.feed_id.is_(None))
    existing_keys = {
        dedupe_key(value) for value in (await db.execute(existing_query)).scalars().all()
    }

    created = 0
    skipped = 0
    invalid: list[str] = []
    created_fqdns: list[Target] = []

    for chunk in payload.entries:
        for raw_line in chunk.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parsed = manager.parse_entry(line)
            if parsed is None:
                invalid.append(line)
                continue
            try:
                validate_destination(parsed.value, parsed.destination_type, settings.router_driver)
            except ValueError:
                invalid.append(line)
                continue

            key = dedupe_key(parsed.value)
            if key in existing_keys:
                skipped += 1
                continue

            description = payload.description or parsed.description or None
            new_target = Target(
                value=parsed.value,
                destination_type=parsed.destination_type,
                description=description,
                enabled=True,
                folder_id=payload.folder_id,
            )
            try:
                async with db.begin_nested():
                    db.add(new_target)
                    await db.flush()
            except IntegrityError:
                skipped += 1
                existing_keys.add(key)
                continue
            existing_keys.add(key)
            created += 1
            if new_target.destination_type == "fqdn":
                created_fqdns.append(new_target)

    if created:
        await db.commit()
        from app.core.engine import bump_policy_version

        bump_policy_version()
        for t in created_fqdns:
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
    try:
        routes = await driver.get_current_routes()
    except asyncio.CancelledError:
        raise
    except ServiceError:
        raise
    except Exception as ex:
        logger.warning("Failed to read active routes from driver '%s': %s", driver.driver_name, ex)
        raise ServiceError(
            status_code=502,
            detail=sanitize_driver_error(ex, driver.driver_name, "reading active routes"),
        ) from ex
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
