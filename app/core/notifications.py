"""Optional generic webhook for transitions, without network destinations or credentials."""

import json
import logging

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.db.models import Feed, RuntimeState
from app.db.session import AsyncSessionLocal
from app.drivers.base import SyncResult

logger = logging.getLogger("tebypass.notifications")


async def notify_transition(result: SyncResult) -> None:
    if not settings.notification_url:
        return
    async with AsyncSessionLocal() as session:
        blocked = bool(
            (
                await session.execute(
                    select(Feed.id).where(Feed.enabled.is_(True), Feed.blocked.is_(True)).limit(1)
                )
            ).scalar()
        )
        state = json.dumps(
            {
                "blocked": blocked,
                "success": result.success,
                "routing_enabled": settings.routing_enabled,
            },
            sort_keys=True,
        )
        previous = await session.get(RuntimeState, "notification")
        if previous and previous.value == state:
            return
        headers = (
            {"Authorization": "Bearer " + settings.notification_token}
            if settings.notification_token
            else {}
        )
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.post(
                    settings.notification_url,
                    headers=headers,
                    json={"service": "tebypass", **json.loads(state)},
                )
                response.raise_for_status()
        except Exception:
            logger.warning("Transition webhook delivery failed; will retry next cycle")
            return
        if previous:
            previous.value = state
        else:
            session.add(RuntimeState(key="notification", value=state))
        await session.commit()
