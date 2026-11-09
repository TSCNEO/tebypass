"""Remote feeds management endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.feeds import FeedManager
from app.db.models import Feed, Target
from app.db.session import get_db
from app.models.schemas import FeedCreate, FeedResponse

router = APIRouter(prefix="/api/v1/feeds", tags=["Feeds"])


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
    return _to_response(await _load_feed(db, feed_id))


@router.post("/{feed_id}/fetch", response_model=dict[str, int])
async def fetch_feed_now(
    feed_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Force download and synchronization of a specific remote feed."""
    feed = await _load_feed(db, feed_id)

    manager = FeedManager()
    parsed_targets, new_etag, modified = await manager.fetch_feed(
        url=feed.url,
        feed_format=feed.format,
        etag=feed.etag,
    )

    if not modified:
        return {"imported": 0, "status": 304}

    feed.last_fetched_at = datetime.now(UTC)
    if new_etag:
        feed.etag = new_etag

    # Delete previous targets from this feed to avoid stale entries
    del_query = select(Target).where(Target.feed_id == feed.id)
    old_targets = (await db.execute(del_query)).scalars().all()
    for old_target in old_targets:
        await db.delete(old_target)

    count = 0
    for parsed in parsed_targets:
        target = Target(
            feed_id=feed.id,
            value=parsed.value,
            destination_type=parsed.destination_type,
            description=parsed.description or f"Imported from {feed.name}",
            enabled=True,
        )
        db.add(target)
        count += 1

    await db.commit()
    return {"imported": count}
