"""Remote feeds management endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.feeds import FeedManager
from app.db.models import Feed, Target
from app.db.session import get_db
from app.models.schemas import FeedCreate, FeedResponse

router = APIRouter(prefix="/api/v1/feeds", tags=["Feeds"])


@router.get("", response_model=list[FeedResponse])
async def list_feeds(db: AsyncSession = Depends(get_db)) -> list[FeedResponse]:
    """List all registered remote feeds."""
    query = select(Feed).order_by(Feed.created_at.desc())
    result = await db.execute(query)
    feeds = result.scalars().all()
    responses: list[FeedResponse] = []
    for f in feeds:
        responses.append(
            FeedResponse(
                id=f.id,
                name=f.name,
                url=f.url,
                format=f.format,
                enabled=f.enabled,
                last_fetched_at=f.last_fetched_at,
                created_at=f.created_at,
                targets_count=len(f.targets) if f.targets else 0,
            )
        )
    return responses


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
    await db.refresh(feed)

    return FeedResponse(
        id=feed.id,
        name=feed.name,
        url=feed.url,
        format=feed.format,
        enabled=feed.enabled,
        last_fetched_at=feed.last_fetched_at,
        created_at=feed.created_at,
        targets_count=0,
    )


@router.delete("/{feed_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_feed(
    feed_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a feed and all associated targets."""
    query = select(Feed).where(Feed.id == feed_id)
    feed = (await db.execute(query)).scalar_one_or_none()
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")

    await db.delete(feed)
    await db.commit()


@router.post("/{feed_id}/fetch", response_model=dict[str, int])
async def fetch_feed_now(
    feed_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Force download and synchronization of a specific remote feed."""
    query = select(Feed).where(Feed.id == feed_id)
    feed = (await db.execute(query)).scalar_one_or_none()
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")

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
    for ot in old_targets:
        await db.delete(ot)

    count = 0
    for pt in parsed_targets:
        target = Target(
            feed_id=feed.id,
            value=pt.value,
            destination_type=pt.destination_type,
            description=pt.description or f"Imported from {feed.name}",
            enabled=True,
        )
        db.add(target)
        count += 1

    await db.commit()
    return {"imported": count}
