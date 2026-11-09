"""Remote feeds management endpoints."""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.service_errors import ServiceError
from app.db.models import Feed, Target
from app.models.schemas import FeedCreate, FeedResponse

logger = logging.getLogger("tebypass.api.feeds")


def _to_response(feed: Feed) -> FeedResponse:
    return FeedResponse(
        id=feed.id,
        name=feed.name,
        url=feed.url,
        format=feed.format,
        enabled=feed.enabled,
        last_fetched_at=feed.last_fetched_at,
        created_at=feed.created_at,
        targets_count=len(feed.targets) if feed.targets else 0,
        blocked=feed.blocked,
        source_updated_at=feed.source_updated_at,
        last_checked_at=feed.last_checked_at,
        last_error=feed.last_error,
    )


async def _load_feed(db: AsyncSession, feed_id: str) -> Feed:
    query = select(Feed).where(Feed.id == feed_id).options(selectinload(Feed.targets))
    feed = (await db.execute(query)).scalar_one_or_none()
    if feed is None:
        raise ServiceError(status_code=404, detail="Feed not found")
    return feed


async def list_feeds(db: AsyncSession) -> list[FeedResponse]:
    """List all registered remote feeds."""
    query = select(Feed).options(selectinload(Feed.targets)).order_by(Feed.created_at.desc())
    feeds = (await db.execute(query)).scalars().all()
    return [_to_response(feed) for feed in feeds]


async def create_feed(
    payload: FeedCreate,
    db: AsyncSession,
) -> FeedResponse:
    """Register a new remote target feed."""
    existing_query = select(Feed).where(Feed.url == payload.url)
    existing = (await db.execute(existing_query)).scalar_one_or_none()
    if existing:
        raise ServiceError(status_code=400, detail="A feed with this URL already exists")

    feed = Feed(
        name=payload.name,
        url=payload.url,
        format=payload.format,
        enabled=payload.enabled,
    )
    db.add(feed)
    await db.commit()

    return _to_response(await _load_feed(db, feed.id))


async def delete_feed(
    feed_id: str,
    db: AsyncSession,
) -> None:
    """Delete a feed and all associated targets."""
    feed = await _load_feed(db, feed_id)
    await db.delete(feed)
    await db.commit()
    from app.core.runtime import trigger_reconciliation

    await trigger_reconciliation(force=False)


async def toggle_feed(
    feed_id: str,
    db: AsyncSession,
) -> FeedResponse:
    """Enable or disable a feed without deleting it."""
    feed = await _load_feed(db, feed_id)
    feed.enabled = not feed.enabled
    await db.commit()

    try:
        from app.core.runtime import trigger_reconciliation

        asyncio.create_task(trigger_reconciliation(force=True))
    except Exception as ex:
        logger.warning("Trigger reconciliation after feed toggle failed: %s", ex)

    return _to_response(await _load_feed(db, feed_id))


async def fetch_feed_now(
    feed_id: str,
    db: AsyncSession,
) -> dict[str, int]:
    """Force download and synchronization of a specific remote feed."""
    feed = await _load_feed(db, feed_id)
    from app.core.feeds_sync import refresh_feed

    imported = await refresh_feed(db, feed, force=True)

    try:
        from app.core.runtime import trigger_reconciliation

        asyncio.create_task(trigger_reconciliation(force=True))
    except Exception as ex:
        logger.warning("Trigger reconciliation after feed fetch failed: %s", ex)

    return {"imported": imported}


async def clear_feed_targets(
    feed_id: str,
    db: AsyncSession,
) -> dict[str, int]:
    """Wipe all targets imported from a feed."""
    feed = await _load_feed(db, feed_id)
    del_query = select(Target).where(Target.feed_id == feed.id)
    targets = (await db.execute(del_query)).scalars().all()
    count = len(targets)
    for t in targets:
        await db.delete(t)
    feed.enabled = False
    feed.blocked = False
    feed.etag = None
    feed.last_fetched_at = None
    await db.commit()

    try:
        from app.core.runtime import trigger_reconciliation

        asyncio.create_task(trigger_reconciliation(force=True))
    except Exception as ex:
        logger.warning("Trigger reconciliation after feed clear failed: %s", ex)

    return {"deleted": count}
