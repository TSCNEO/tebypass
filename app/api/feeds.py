"""Remote feeds management endpoints."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Feed, Target
from app.db.session import get_db
from app.models.schemas import FeedCreate, FeedResponse

router = APIRouter(prefix="/api/v1/feeds", tags=["Feeds"])
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
    )


async def _load_feed(db: AsyncSession, feed_id: str) -> Feed:
    query = select(Feed).where(Feed.id == feed_id).options(selectinload(Feed.targets))
    feed = (await db.execute(query)).scalar_one_or_none()
    if feed is None:
        raise HTTPException(status_code=404, detail="Feed not found")
    return feed


@router.get("", response_model=list[FeedResponse])
async def list_feeds(db: AsyncSession = Depends(get_db)) -> list[FeedResponse]:
    """List all registered remote feeds."""
    query = select(Feed).options(selectinload(Feed.targets)).order_by(Feed.created_at.desc())
    feeds = (await db.execute(query)).scalars().all()
    return [_to_response(feed) for feed in feeds]


@router.post("", response_model=FeedResponse, status_code=status.HTTP_201_CREATED)
async def create_feed(
    payload: FeedCreate,
    db: AsyncSession = Depends(get_db),
) -> FeedResponse:
    """Register a new remote target feed."""
    existing_query = select(Feed).where(Feed.url == payload.url)
    existing = (await db.execute(existing_query)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="A feed with this URL already exists")

    feed = Feed(
        name=payload.name,
        url=payload.url,
        format=payload.format,
        enabled=payload.enabled,
    )
    db.add(feed)
    await db.commit()

    return _to_response(await _load_feed(db, feed.id))


@router.delete("/{feed_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_feed(
    feed_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a feed and all associated targets."""
    feed = await _load_feed(db, feed_id)
    await db.delete(feed)
    await db.commit()


@router.post("/{feed_id}/toggle", response_model=FeedResponse)
async def toggle_feed(
    feed_id: str,
    db: AsyncSession = Depends(get_db),
) -> FeedResponse:
    """Enable or disable a feed without deleting it."""
    feed = await _load_feed(db, feed_id)
    feed.enabled = not feed.enabled
    await db.commit()

    try:
        from app.main import trigger_reconciliation

        asyncio.create_task(trigger_reconciliation(force=True))
    except Exception as ex:
        logger.warning("Trigger reconciliation after feed toggle failed: %s", ex)

    return _to_response(await _load_feed(db, feed_id))


@router.post("/{feed_id}/fetch", response_model=dict[str, int])
async def fetch_feed_now(
    feed_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Force download and synchronization of a specific remote feed."""
    feed = await _load_feed(db, feed_id)
    from app.core.feeds_sync import refresh_feed

    imported = await refresh_feed(db, feed, force=True)

    try:
        from app.main import trigger_reconciliation

        asyncio.create_task(trigger_reconciliation(force=True))
    except Exception as ex:
        logger.warning("Trigger reconciliation after feed fetch failed: %s", ex)

    return {"imported": imported}


@router.post("/{feed_id}/clear", response_model=dict[str, int])
async def clear_feed_targets(
    feed_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Wipe all targets imported from a feed."""
    feed = await _load_feed(db, feed_id)
    del_query = select(Target).where(Target.feed_id == feed.id)
    targets = (await db.execute(del_query)).scalars().all()
    count = len(targets)
    for t in targets:
        await db.delete(t)
    feed.etag = None
    feed.last_fetched_at = None
    await db.commit()

    try:
        from app.main import trigger_reconciliation

        asyncio.create_task(trigger_reconciliation(force=True))
    except Exception as ex:
        logger.warning("Trigger reconciliation after feed clear failed: %s", ex)

    return {"deleted": count}
