"""HTTP adapter for core services."""

from fastapi import APIRouter, status

from app.api.dependencies import DatabaseSession
from app.core import feeds_service as service
from app.models.schemas import FeedCreate, FeedResponse

router = APIRouter(prefix="/api/v1/feeds", tags=["Feeds"])


@router.get("", response_model=list[FeedResponse])
async def list_feeds(db: DatabaseSession) -> list[FeedResponse]:
    return await service.list_feeds(db)


@router.post("", response_model=FeedResponse, status_code=status.HTTP_201_CREATED)
async def create_feed(payload: FeedCreate, db: DatabaseSession) -> FeedResponse:
    return await service.create_feed(payload, db)


@router.delete("/{feed_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_feed(feed_id: str, db: DatabaseSession) -> None:
    return await service.delete_feed(feed_id, db)


@router.post("/{feed_id}/toggle", response_model=FeedResponse)
async def toggle_feed(feed_id: str, db: DatabaseSession) -> FeedResponse:
    return await service.toggle_feed(feed_id, db)


@router.post("/{feed_id}/fetch", response_model=dict[str, int])
async def fetch_feed_now(feed_id: str, db: DatabaseSession) -> dict[str, int]:
    return await service.fetch_feed_now(feed_id, db)


@router.post("/{feed_id}/clear", response_model=dict[str, int])
async def clear_feed_targets(feed_id: str, db: DatabaseSession) -> dict[str, int]:
    return await service.clear_feed_targets(feed_id, db)
