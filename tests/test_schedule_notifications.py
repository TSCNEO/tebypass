"""Schedules affect community contributions only; webhooks deduplicate transitions."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx

from app.core.config import settings
from app.core.engine import ReconciliationEngine
from app.core.notifications import notify_transition
from app.db.models import Feed, Target
from app.db.session import AsyncSessionLocal
from app.drivers.base import SyncResult
from app.drivers.mock import MockRouterDriver


async def test_closed_schedule_keeps_manual_targets(db_ready: None) -> None:
    settings.active_days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][
        (datetime.now(UTC).weekday() + 1) % 7
    ]
    async with AsyncSessionLocal() as session:
        feed = Feed(
            name="Community",
            url="https://feed.example.net/status",
            blocked=True,
            last_fetched_at=datetime.now(UTC),
        )
        generic = Feed(
            name="Static",
            url="https://feed.example.net/static",
            format="plaintext",
            last_fetched_at=datetime.now(UTC),
        )
        session.add_all([feed, generic])
        await session.flush()
        session.add_all(
            [
                Target(value="198.51.100.7/32", destination_type="cidr", feed_id=feed.id),
                Target(value="203.0.113.7/32", destination_type="cidr"),
                Target(value="192.0.2.7/32", destination_type="cidr", feed_id=generic.id),
            ]
        )
        await session.commit()
    driver = MockRouterDriver()
    assert (await ReconciliationEngine(driver=driver).reconcile()).success
    assert await driver.get_current_routes() == {"203.0.113.7/32", "192.0.2.7/32"}


async def test_webhook_only_sends_transitions(db_ready: None) -> None:
    settings.notification_url = "https://events.example.net/tebypass"
    response = httpx.Response(200, request=httpx.Request("POST", settings.notification_url))
    send = AsyncMock(return_value=response)
    with patch("httpx.AsyncClient.post", send):
        await notify_transition(SyncResult("mock", 0, 0, 0, 0, True))
        await notify_transition(SyncResult("mock", 0, 0, 0, 0, True))
        assert send.await_count == 1
        await notify_transition(SyncResult("mock", 0, 0, -1, 0, False))
        assert send.await_count == 2
