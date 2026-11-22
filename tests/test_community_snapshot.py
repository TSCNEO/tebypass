"""Global OFF, malformed feeds and snapshot persistence regression tests."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.core.community import parse_snapshot
from app.core.config import settings
from app.core.engine import ReconciliationEngine
from app.core.feeds_sync import refresh_feed
from app.db.models import Feed, Target
from app.db.session import AsyncSessionLocal
from app.drivers.mock import MockRouterDriver


def payload(blocked: bool = False) -> dict[str, object]:
    now = datetime.now(UTC).isoformat()
    return {
        "lastUpdate": now,
        "isBlocked": blocked,
        "data": [
            {
                "ip": "198.51.100.7",
                "isp": "ISP_A",
                "stateChanges": [{"state": True, "timestamp": now}],
            }
        ],
    }


def test_global_off_is_independent_of_residual_destinations() -> None:
    snapshot = parse_snapshot(payload())
    assert not snapshot.blocked
    assert snapshot.destinations == ["198.51.100.7"]


def test_stale_snapshot_rejected() -> None:
    data = payload()
    data["lastUpdate"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    with pytest.raises(Exception, match="stale"):
        parse_snapshot(data)


async def test_global_off_removes_only_feed_contribution(db_ready: None) -> None:
    async with AsyncSessionLocal() as session:
        feed = Feed(name="Community", url="https://feed.example.net/status", format="hayahora")
        session.add(feed)
        await session.flush()
        session.add(Target(value="203.0.113.7/32", destination_type="cidr"))
        with patch(
            "app.core.feeds_sync.download", AsyncMock(return_value=(json.dumps(payload()), None))
        ):
            await refresh_feed(session, feed)
    driver = MockRouterDriver()
    result = await ReconciliationEngine(driver=driver).reconcile()
    assert result.success
    assert await driver.get_current_routes() == {"203.0.113.7/32"}


async def test_invalid_refresh_preserves_last_valid_snapshot(db_ready: None) -> None:
    async with AsyncSessionLocal() as session:
        feed = Feed(name="Community", url="https://feed.example.net/status", format="hayahora")
        session.add(feed)
        await session.flush()
        with patch(
            "app.core.feeds_sync.download",
            AsyncMock(return_value=(json.dumps(payload(True)), '"v1"')),
        ):
            assert await refresh_feed(session, feed) == 1
        with patch(
            "app.core.feeds_sync.download",
            AsyncMock(return_value=("<html>example.org</html>", None)),
        ):
            assert await refresh_feed(session, feed) == 0
        assert feed.blocked is True
        assert feed.etag == '"v1"'
        assert feed.last_error
        assert len((await session.execute(select(Target))).scalars().all()) == 1


async def test_global_off_purges_feed_targets_and_force_active_restores_them(
    db_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with AsyncSessionLocal() as session:
        feed = Feed(name="Community", url="https://feed.example.net/status", format="hayahora")
        session.add(feed)
        await session.flush()
        session.add(Target(value="203.0.113.7/32", destination_type="cidr"))

        with patch(
            "app.core.feeds_sync.download",
            AsyncMock(return_value=(json.dumps(payload(True)), '"on"')),
        ):
            assert await refresh_feed(session, feed) == 1

        feed_targets = (
            (await session.execute(select(Target).where(Target.feed_id == feed.id))).scalars().all()
        )
        assert [t.value for t in feed_targets] == ["198.51.100.7/32"]

        with patch(
            "app.core.feeds_sync.download",
            AsyncMock(return_value=(json.dumps(payload(False)), '"off"')),
        ):
            assert await refresh_feed(session, feed) == 0

        assert feed.blocked is False
        assert (
            await session.execute(select(Target).where(Target.feed_id == feed.id))
        ).scalars().all() == []
        manual_targets = (
            (await session.execute(select(Target).where(Target.feed_id.is_(None)))).scalars().all()
        )
        assert [t.value for t in manual_targets] == ["203.0.113.7/32"]

        monkeypatch.setattr(settings, "feed_force_active", True)
        with patch(
            "app.core.feeds_sync.download",
            AsyncMock(return_value=(json.dumps(payload(False)), '"forced"')),
        ):
            assert await refresh_feed(session, feed) == 1

        restored = (
            (await session.execute(select(Target).where(Target.feed_id == feed.id))).scalars().all()
        )
        assert [t.value for t in restored] == ["198.51.100.7/32"]


async def test_hayahora_refresh_uses_streaming_compact_history(db_ready: None) -> None:
    now = datetime.now(UTC)
    older = (now - timedelta(hours=3)).isoformat()
    newer = (now - timedelta(minutes=5)).isoformat()
    doc = {
        "lastUpdate": now.isoformat(),
        "isBlocked": True,
        "data": [
            {
                "ip": "198.51.100.10",
                "isp": "ISP_A",
                "description": "Cloudflare",
                "stateChanges": [
                    {"state": True, "timestamp": older},
                    {"state": False, "timestamp": newer},
                ],
            },
            {
                "ip": "198.51.100.11",
                "isp": "ISP_A",
                "description": "Cloudflare",
                "stateChanges": [
                    {"state": False, "timestamp": older},
                    {"state": True, "timestamp": newer},
                ],
            },
        ],
    }

    async with AsyncSessionLocal() as session:
        feed = Feed(name="Community", url="https://feed.example.net/status", format="hayahora")
        session.add(feed)
        await session.flush()
        with patch(
            "app.core.feeds_sync.download",
            AsyncMock(return_value=(json.dumps(doc), '"stream"')),
        ):
            assert await refresh_feed(session, feed) == 1
        imported = (
            (await session.execute(select(Target).where(Target.feed_id == feed.id))).scalars().all()
        )
        assert [t.value for t in imported] == ["198.51.100.11/32"]
