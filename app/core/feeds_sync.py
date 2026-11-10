"""Feed refresh orchestration: download, parse and persist feed targets.

This module keeps the reconciliation engine decoupled from HTTP details and from the
feed format: the engine only asks "refresh whatever is due" and gets a count back.
"""

import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.community import parse_snapshot
from app.core.config import settings
from app.core.feed_http import download
from app.core.feeds import FeedManager
from app.core.targets import as_utc
from app.db.models import Feed, Target
from app.drivers.capabilities import validate_destination

logger = logging.getLogger("tebypass.feeds_sync")


async def refresh_feed(session: AsyncSession, feed: Feed, force: bool = False) -> int:
    """Download a single feed and replace its targets. Returns the imported count.

    A failed download never wipes the previously imported targets: stale routes are far
    less harmful than silently dropping every route because of one network hiccup.
    """
    now = datetime.now(UTC)
    manager = FeedManager()

    feed.last_checked_at = now
    try:
        content, new_etag = await download(feed.url, None if force else feed.etag)
        if content is None:
            if (
                feed.source_updated_at
                and (now - as_utc(feed.source_updated_at)).total_seconds()
                > settings.feed_max_age_seconds
            ):
                raise ValueError("Community feed is stale")
            feed.last_fetched_at = now
            feed.last_error = None
            await session.commit()
            return 0
        payload = json.loads(content) if feed.format != "plaintext" else None
        community = feed.format in {"hayahora", "stopliga"} or (
            isinstance(payload, dict) and isinstance(payload.get("data"), list)
        )
        if community:
            snapshot = parse_snapshot(payload, settings.feed_force_active, settings.feed_isp_filter)
            parsed = [
                item for value in snapshot.destinations if (item := manager.parse_entry(value))
            ]
        else:
            parsed = manager.parse_content(content, feed_format=feed.format)
        if len(parsed) > settings.feed_max_targets:
            raise ValueError("Feed exceeds target safety ceiling")
        for item in parsed:
            validate_destination(item.value, item.destination_type, settings.router_driver)
    except Exception as ex:
        # Keep the last valid contribution on unknown/error; never reinterpret it as OFF.
        feed.last_error = f"Feed refresh failed ({type(ex).__name__})"
        await session.commit()
        logger.warning("Feed refresh rejected (%s)", type(ex).__name__)
        return 0
    feed.last_error = None
    feed.blocked = snapshot.blocked if community else None
    feed.source_updated_at = snapshot.updated_at if community else None
    existing = {
        target.value: target
        for target in (await session.execute(select(Target).where(Target.feed_id == feed.id)))
        .scalars()
        .all()
    }

    imported = 0
    seen: set[str] = set()
    for item in parsed:
        if item.value in seen:
            continue
        seen.add(item.value)
        target = existing.pop(item.value, None)
        if target is None:
            session.add(
                Target(
                    feed_id=feed.id,
                    value=item.value,
                    destination_type=item.destination_type,
                    description=item.description or f"Imported from {feed.name}",
                    enabled=True,
                )
            )
        else:
            target.description = item.description or f"Imported from {feed.name}"
            target.enabled = True
        imported += 1
    for target in existing.values():
        await session.delete(target)

    feed.last_fetched_at = now
    feed.etag = new_etag
    await session.commit()

    logger.info("Feed '%s' refreshed: %d targets", feed.name, imported)
    return imported


async def refresh_feed_by_id(session: AsyncSession, feed_id: str) -> int:
    """Refresh a single feed looked up by id."""
    feed = (await session.execute(select(Feed).where(Feed.id == feed_id))).scalar_one_or_none()
    if feed is None:
        return 0
    return await refresh_feed(session, feed)


async def refresh_due_feeds(
    session: AsyncSession,
    interval_seconds: int,
    force: bool = False,
) -> int:
    """Refresh every enabled feed whose last successful fetch is older than the interval."""
    if interval_seconds <= 0 and not force:
        return 0

    cutoff = datetime.now(UTC) - timedelta(seconds=interval_seconds)
    feeds = (await session.execute(select(Feed).where(Feed.enabled.is_(True)))).scalars().all()

    refreshed = 0
    for feed in feeds:
        last = feed.last_fetched_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if not force and last is not None and last > cutoff:
            continue
        await refresh_feed(session, feed, force=force)
        refreshed += 1
    return refreshed
