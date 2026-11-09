"""First-run bootstrap: seed the community feed so a fresh install is not empty.

Everything else (router credentials, intervals, feed toggles) is configured from the web
interface and persisted in SQLite, so this module only guarantees that the community feed
the project is designed around exists at least once.
"""

import logging

from sqlalchemy import select

from app.core.config import settings
from app.db.models import Feed
from app.db.session import AsyncSessionLocal

logger = logging.getLogger("tebypass.bootstrap")

COMMUNITY_FEED_NAME = "HayAHora / StopLiga (LaLiga)"


async def seed_default_feed() -> None:
    """Create the community feed row if no feed has ever been registered."""
    async with AsyncSessionLocal() as session:
        existing = (await session.execute(select(Feed.id).limit(1))).scalar_one_or_none()
        if existing is not None:
            return

        session.add(
            Feed(
                name=COMMUNITY_FEED_NAME,
                url=settings.hayahora_feed_url,
                format="hayahora",
                enabled=False,
            )
        )
        await session.commit()
        logger.info("Seeded community feed '%s' (disabled by default)", COMMUNITY_FEED_NAME)
