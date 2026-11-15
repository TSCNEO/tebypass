"""Feed refresh orchestration: download, parse and persist feed targets.

This module keeps the reconciliation engine decoupled from HTTP details and from the
feed format: the engine only asks "refresh whatever is due" and gets a count back.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from weakref import WeakKeyDictionary

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.community import InvalidISPFilterError, parse_snapshot
from app.core.config import settings
from app.core.feed_http import download
from app.core.feeds import FeedManager
from app.core.targets import as_utc
from app.db.models import Feed, Target
from app.db.session import AsyncSessionLocal
from app.drivers.capabilities import validate_destination

logger = logging.getLogger("tebypass.feeds_sync")

_FEED_LOCKS: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = (
    WeakKeyDictionary()
)


def feed_lock(feed_id: str) -> asyncio.Lock:
    """Return a loop-scoped lock serializing refreshes for one feed without holding ROUTING_LOCK."""
    loop = asyncio.get_running_loop()
    per_loop = _FEED_LOCKS.setdefault(loop, {})
    lock = per_loop.get(feed_id)
    if lock is None:
        lock = asyncio.Lock()
        per_loop[feed_id] = lock
    return lock


async def refresh_feed(session: AsyncSession, feed: Feed, force: bool = False) -> int:
    """Download a single feed and replace its targets. Returns the imported count.

    A failed download never wipes the previously imported targets: stale routes are far
    less harmful than silently dropping every route because of one network hiccup.
    """
    now = datetime.now(UTC)
    manager = FeedManager()
    url = feed.url
    etag = None if force else feed.etag
    feed_format = feed.format
    feed_name = feed.name
    source_updated_at = feed.source_updated_at

    try:
        content, new_etag = await download(url, etag)
        feed.last_checked_at = now
        if content is None:
            if (
                source_updated_at
                and (now - as_utc(source_updated_at)).total_seconds()
                > settings.feed_max_age_seconds
            ):
                raise ValueError("Community feed is stale")
            feed.last_fetched_at = now
            feed.last_error = None
            await session.commit()
            return 0
        payload = json.loads(content) if feed_format != "plaintext" else None
        community = feed_format in {"hayahora", "stopliga"} or (
            isinstance(payload, dict) and isinstance(payload.get("data"), list)
        )
        if community:
            snapshot = parse_snapshot(payload, settings.feed_force_active, settings.feed_isp_filter)
            parsed = [
                item for value in snapshot.destinations if (item := manager.parse_entry(value))
            ]
        else:
            parsed = manager.parse_content(content, feed_format=feed_format)
        if len(parsed) > settings.feed_max_targets:
            raise ValueError("Feed exceeds target safety ceiling")
        for item in parsed:
            validate_destination(item.value, item.destination_type, settings.router_driver)
    except asyncio.CancelledError:
        raise
    except InvalidISPFilterError as ex:
        # Keep the previous valid targets/etag/snapshot while surfacing actionable ISP options.
        feed.last_checked_at = now
        feed.last_error = f"{ex}. Se conserva el último estado válido anterior."
        await session.commit()
        logger.warning("Feed refresh rejected due to invalid ISP filter: %s", ex.isp)
        return 0
    except Exception as ex:
        # Keep the last valid contribution on unknown/error; never reinterpret it as OFF.
        feed.last_checked_at = now
        feed.last_error = f"Feed refresh failed ({type(ex).__name__})"
        await session.commit()
        logger.warning("Feed refresh rejected (%s)", type(ex).__name__)
        return 0
    feed.last_error = None
    feed.blocked = snapshot.blocked if community else None
    feed.source_updated_at = snapshot.updated_at if community else None
    existing = {
        target.value: target
        for target in (await session.execute(select(Target).where(Target.feed_id == feed.id)))
        .scalars()
        .all()
    }

    imported = 0
    seen: set[str] = set()
    for item in parsed:
        if item.value in seen:
            continue
        seen.add(item.value)
        target = existing.pop(item.value, None)
        if target is None:
            session.add(
                Target(
                    feed_id=feed.id,
                    value=item.value,
                    destination_type=item.destination_type,
                    description=item.description or f"Imported from {feed_name}",
                    enabled=True,
                )
            )
        else:
            target.description = item.description or f"Imported from {feed_name}"
            target.enabled = True
        imported += 1
    for target in existing.values():
        await session.delete(target)

    feed.last_fetched_at = now
    feed.etag = new_etag
    await session.commit()

    logger.info("Feed '%s' refreshed: %d targets", feed_name, imported)
    return imported


async def refresh_feed_by_id(session: AsyncSession, feed_id: str) -> int:
    """Refresh a single feed looked up by id."""
    feed = (await session.execute(select(Feed).where(Feed.id == feed_id))).scalar_one_or_none()
    if feed is None:
        return 0
    return await refresh_feed(session, feed)


async def refresh_due_feeds(
    session: AsyncSession,
    interval_seconds: int,
    force: bool = False,
    *,
    abort_event: asyncio.Event | None = None,
) -> int:
    """Refresh due enabled feeds concurrently with bounded concurrency and total budget."""
    if interval_seconds <= 0 and not force:
        return 0

    cutoff = datetime.now(UTC) - timedelta(seconds=interval_seconds)
    feeds = (await session.execute(select(Feed).where(Feed.enabled.is_(True)))).scalars().all()
    due_ids: list[str] = []
    for feed in feeds:
        last = feed.last_fetched_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if not force and last is not None and last > cutoff:
            continue
        due_ids.append(feed.id)

    if session.in_transaction():
        await session.commit()

    if not due_ids or (abort_event is not None and abort_event.is_set()):
        return 0

    sem = asyncio.Semaphore(max(1, settings.feed_concurrency))
    refreshed = 0

    async def _refresh_one(feed_id: str) -> None:
        nonlocal refreshed
        async with sem:
            if abort_event is not None and abort_event.is_set():
                return
            async with feed_lock(feed_id):
                if abort_event is not None and abort_event.is_set():
                    return
                async with AsyncSessionLocal() as feed_session:
                    feed = await feed_session.get(Feed, feed_id)
                    if feed is None or not feed.enabled:
                        return
                    await refresh_feed(feed_session, feed, force=force)
                    refreshed += 1

    tasks = {asyncio.create_task(_refresh_one(feed_id)) for feed_id in due_ids}
    abort_task: asyncio.Task[bool] | None = (
        asyncio.create_task(abort_event.wait()) if abort_event is not None else None
    )
    deadline = asyncio.get_running_loop().time() + max(0.05, settings.feed_budget_seconds)

    try:
        while tasks:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                logger.warning(
                    "Feed refresh budget (%.1fs) reached; %d/%d feeds completed",
                    settings.feed_budget_seconds,
                    refreshed,
                    len(due_ids),
                )
                break
            wait_set: set[asyncio.Task[Any]] = set(tasks)
            if abort_task is not None and not abort_task.done():
                wait_set.add(abort_task)
            done, _ = await asyncio.wait(
                wait_set,
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                logger.warning(
                    "Feed refresh budget (%.1fs) reached; %d/%d feeds completed",
                    settings.feed_budget_seconds,
                    refreshed,
                    len(due_ids),
                )
                break
            if abort_task is not None and abort_task in done:
                break
            tasks.difference_update(done)
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if abort_task is not None:
            if not abort_task.done():
                abort_task.cancel()
            await asyncio.gather(abort_task, return_exceptions=True)

    return refreshed
