"""Shared atomic persistence for DNS resolutions (ResolvedIP)."""

import logging
import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import case, delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import CachedResolution
from app.core.targets import as_utc
from app.db.models import ResolvedIP, Target

logger = logging.getLogger("tebypass.resolution_store")


def _dedupe_resolutions(resolutions: Sequence[CachedResolution]) -> list[CachedResolution]:
    """Collapse duplicate IP entries from one resolution batch, preserving longest expiry."""
    by_ip: dict[str, CachedResolution] = {}
    for entry in resolutions:
        existing = by_ip.get(entry.ip_address)
        if existing is None or as_utc(entry.expires_at) > as_utc(existing.expires_at):
            by_ip[entry.ip_address] = entry
    return list(by_ip.values())


async def upsert_resolved_ips(
    session: AsyncSession,
    target_id: str,
    resolutions: Sequence[CachedResolution],
    now: datetime,
) -> bool:
    """Atomically insert or extend ResolvedIP rows for a target without shortening retention.

    Returns ``False`` when the target no longer exists (e.g. deleted concurrently), so
    callers can skip it without aborting unrelated destinations.
    """
    unique_entries = _dedupe_resolutions(resolutions)
    if not unique_entries:
        return True

    rows = [
        {
            "id": str(uuid.uuid4()),
            "target_id": target_id,
            "ip_address": entry.ip_address,
            "cidr": entry.cidr,
            "first_seen_at": now,
            "last_seen_at": now,
            "expires_at": as_utc(entry.expires_at),
            "ttl_seconds": entry.dns_ttl,
        }
        for entry in unique_entries
    ]

    stmt = sqlite_insert(ResolvedIP).values(rows)
    longer_expiry = func.julianday(stmt.excluded.expires_at) > func.julianday(ResolvedIP.expires_at)
    newer_seen = func.julianday(stmt.excluded.last_seen_at) > func.julianday(
        ResolvedIP.last_seen_at
    )
    upsert_stmt = stmt.on_conflict_do_update(
        index_elements=[ResolvedIP.target_id, ResolvedIP.ip_address],
        set_={
            "cidr": stmt.excluded.cidr,
            "last_seen_at": case(
                (newer_seen, stmt.excluded.last_seen_at),
                else_=ResolvedIP.last_seen_at,
            ),
            "expires_at": case(
                (longer_expiry, stmt.excluded.expires_at),
                else_=ResolvedIP.expires_at,
            ),
            "ttl_seconds": case(
                (longer_expiry, stmt.excluded.ttl_seconds),
                else_=ResolvedIP.ttl_seconds,
            ),
        },
    )

    try:
        async with session.begin_nested():
            await session.execute(upsert_stmt)
    except IntegrityError:
        still_exists = await session.scalar(select(Target.id).where(Target.id == target_id))
        if still_exists is None:
            logger.debug("Skipping ResolvedIP upsert for concurrently deleted target %s", target_id)
            return False
        raise
    return True


async def delete_expired_resolved_ips(session: AsyncSession, now: datetime) -> None:
    """Remove persisted resolutions whose sliding retention window has expired."""
    await session.execute(
        delete(ResolvedIP)
        .where(ResolvedIP.expires_at <= now)
        .execution_options(synchronize_session=False)
    )


async def load_retained_resolved_ips(
    session: AsyncSession,
    target_ids: set[str],
    now: datetime,
) -> dict[str, list[ResolvedIP]]:
    """Load non-expired ResolvedIP records grouped by target ID."""
    if not target_ids:
        return {}
    rows = (
        (await session.execute(select(ResolvedIP).where(ResolvedIP.expires_at > now)))
        .scalars()
        .all()
    )
    grouped: dict[str, list[ResolvedIP]] = defaultdict(list)
    for entry in rows:
        if entry.target_id in target_ids and as_utc(entry.expires_at) > now:
            grouped[entry.target_id].append(entry)
    return grouped
