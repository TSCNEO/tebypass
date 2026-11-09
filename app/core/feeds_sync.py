"""Feed refresh orchestration: download, parse and persist feed targets.

This module keeps the reconciliation engine decoupled from HTTP details and from the
feed format: the engine only asks "refresh whatever is due" and gets a count back.
"""

import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.feeds import FeedManager
from app.db.models import Feed, Target

logger = logging.getLogger("tebypass.feeds_sync")

USER_AGENT = "TeByPass/0.1.0 (+https://github.com/TSCNEO/tebypass)"


async def refresh_feed(session: AsyncSession, feed: Feed) -> int:
    """Download a single feed and replace its targets. Returns the imported count.

    A failed download never wipes the previously imported targets: stale routes are far
    less harmful than silently dropping every route because of one network hiccup.
    """
    now = datetime.now(UTC)
    manager = FeedManager()

    headers = {"User-Agent": USER_AGENT}
    if feed.etag:
        headers["If-None-Match"] = feed.etag

    try:
        async with httpx.AsyncClient(timeout=manager.timeout) as client:
            response = await client.get(feed.url, headers=headers)
    except Exception as ex:  # noqa: BLE001 - a dead feed must not break the sync loop
        logger.warning("Feed '%s' download failed: %s", feed.name, ex)
        return 0

    if response.status_code == 304:
        feed.last_fetched_at = now
        await session.commit()
        logger.info("Feed '%s' unchanged (HTTP 304)", feed.name)
        return 0

    if response.status_code >= 400:
        logger.warning("Feed '%s' returned HTTP %s", feed.name, response.status_code)
        return 0

    new_etag = response.headers.get("etag")
    parsed = manager.parse_content(
        response.text,
        feed_format=feed.format,
        force_active=settings.feed_force_active,
        isp_filter=settings.feed_isp_filter,
    )

    await session.execute(delete(Target).where(Target.feed_id == feed.id))

    imported = 0
    seen: set[str] = set()
    for item in parsed:
        if item.value in seen:
            continue
        seen.add(item.value)
        session.add(
            Target(
                feed_id=feed.id,
                value=item.value,
                destination_type=item.destination_type,
                description=item.description or f"Imported from {feed.name}",
                enabled=True,
            )
        )
        imported += 1

    feed.last_fetched_at = now
    if new_etag:
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


async def refresh_due_feeds(session: AsyncSession, interval_seconds: int) -> int:
    """Refresh every enabled feed whose last successful fetch is older than the interval."""
    if interval_seconds <= 0:
        return 0

    cutoff = datetime.now(UTC) - timedelta(seconds=interval_seconds)
    feeds = (await session.execute(select(Feed).where(Feed.enabled.is_(True)))).scalars().all()

    refreshed = 0
    for feed in feeds:
        last = feed.last_fetched_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if last is not None and last > cutoff:
            continue
        await refresh_feed(session, feed)
        refreshed += 1
    return refreshed
