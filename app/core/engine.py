"""Reconciliation engine coordinating resolution, state diffing, and driver sync."""

import asyncio
import contextlib
import ipaddress
import logging
import time
from datetime import UTC, datetime

from sqlalchemy import and_, delete, or_, select

from app.core.cache import SlidingWindowCache
from app.core.config import settings
from app.core.feeds_sync import refresh_due_feeds
from app.core.notifications import notify_transition
from app.core.resolver import NativeDNSResolver
from app.core.schedule import community_schedule
from app.core.targets import as_utc
from app.db.models import Feed, ResolvedIP, SyncEvent, Target
from app.db.session import AsyncSessionLocal
from app.drivers import get_driver
from app.drivers.base import BaseRouterDriver, SyncResult, TargetCIDR

logger = logging.getLogger("tebypass.engine")

# All API and periodic engines share one writer lock.
ROUTING_LOCK = asyncio.Lock()


class ReconciliationEngine:
    """Core engine responsible for calculating diffs and keeping router state synchronized."""

    def __init__(
        self,
        driver: BaseRouterDriver | None = None,
        cache: SlidingWindowCache | None = None,
        resolver: NativeDNSResolver | None = None,
        sync_interval_seconds: int = 300,
    ) -> None:
        self._explicit_driver = driver
        self.cache = cache or SlidingWindowCache()
        self.resolver = resolver or NativeDNSResolver(cache=self.cache)
        self.sync_interval = sync_interval_seconds
        self._background_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._is_syncing = ROUTING_LOCK

    @property
    def driver(self) -> BaseRouterDriver:
        """Return the live driver, picking up hot configuration changes."""
        if self._explicit_driver is not None:
            return self._explicit_driver
        return get_driver()

    @driver.setter
    def driver(self, value: BaseRouterDriver | None) -> None:
        self._explicit_driver = value

    async def reconcile(self, force: bool = False) -> SyncResult:
        """Serialize all writes and persist failures, including observation errors."""
        async with self._is_syncing:
            driver = self.driver
            try:
                return await self._reconcile(driver, force)
            except Exception as ex:
                logger.warning("Reconciliation failed: %s", ex)
                result = SyncResult(
                    driver_name=driver.driver_name,
                    added_count=0,
                    removed_count=0,
                    total_active=-1,
                    duration_ms=0,
                    success=False,
                    error_message=str(ex),
                )
                await self._record_result(result, datetime.now(UTC))
                return result

    async def _reconcile(self, driver: BaseRouterDriver, force: bool) -> SyncResult:
        """Reconcile using a driver fixed for the whole cycle."""
        start_time = time.perf_counter()
        now = datetime.now(UTC)

        if not settings.routing_enabled:
            error = None
            current: set[str] | None = None
            try:
                current = await driver.get_current_routes(force=True)
                await driver.teardown()
            except Exception as ex:
                error = str(ex)
                logger.warning("Driver teardown failed while routing disabled: %s", ex)
            result = SyncResult(
                driver_name=driver.driver_name,
                added_count=0,
                removed_count=len(current or ()) if error is None else 0,
                total_active=0 if error is None else (len(current) if current is not None else -1),
                duration_ms=round((time.perf_counter() - start_time) * 1000.0, 2),
                success=error is None,
                error_message=error,
            )
            await self._record_result(result, now)
            return result

        # 0. Refresh any remote feed that is due (respecting its enabled toggle)
        async with AsyncSessionLocal() as feed_session:
            try:
                await refresh_due_feeds(
                    feed_session,
                    settings.sync_interval_seconds,
                    force=force,
                )
            except Exception as ex:  # noqa: BLE001 - never abort a sync because of a feed
                logger.warning("Feed refresh failed: %s", ex)

        # 1. Fetch enabled targets from DB (a disabled feed contributes nothing)
        desired_targets: dict[str, TargetCIDR] = {}

        async with AsyncSessionLocal() as session:
            query = (
                select(Target)
                .join(Feed, Target.feed_id == Feed.id, isouter=True)
                .where(
                    Target.enabled.is_(True),
                    or_(Target.feed_id.is_(None), Feed.enabled.is_(True)),
                    or_(Target.feed_id.is_(None), Feed.blocked.is_(None), Feed.blocked.is_(True)),
                )
            )
            if not community_schedule().is_active():
                query = query.where(
                    or_(
                        Target.feed_id.is_(None),
                        and_(Feed.blocked.is_(None), Feed.format.not_in(["hayahora", "stopliga"])),
                    )
                )
            rows = await session.execute(query)
            targets = rows.scalars().all()

            for t in targets:
                if t.destination_type == "cidr":
                    cidr_val = t.value.strip()
                    desired_targets[cidr_val] = TargetCIDR(
                        cidr=cidr_val,
                        target_id=t.id,
                        description=t.description or f"Direct CIDR target {t.value}",
                    )
                elif t.destination_type == "fqdn":
                    # Resolve FQDN via native DNS resolver
                    resolutions = await self.resolver.resolve_fqdn(
                        fqdn=t.value,
                        window_extension_seconds=settings.sliding_window_seconds,
                    )
                    for r in resolutions:
                        desired_targets[r.cidr] = TargetCIDR(
                            cidr=r.cidr,
                            target_id=t.id,
                            description=f"Resolved from {t.value}",
                            expires_at=r.expires_at,
                        )
                        # Persist or update resolved IP record
                        res_ip_query = select(ResolvedIP).where(
                            ResolvedIP.target_id == t.id,
                            ResolvedIP.ip_address == r.ip_address,
                        )
                        existing_ip = (await session.execute(res_ip_query)).scalar_one_or_none()
                        if existing_ip:
                            existing_ip.last_seen_at = now
                            existing_ip.expires_at = r.expires_at
                        else:
                            session.add(
                                ResolvedIP(
                                    target_id=t.id,
                                    ip_address=r.ip_address,
                                    cidr=r.cidr,
                                    first_seen_at=now,
                                    last_seen_at=now,
                                    expires_at=r.expires_at,
                                    ttl_seconds=r.dns_ttl,
                                )
                            )

                    # Persisted retention survives rotation, transient DNS failure and restart.
                    await session.flush()
                    retained = (
                        (
                            await session.execute(
                                select(ResolvedIP).where(ResolvedIP.target_id == t.id)
                            )
                        )
                        .scalars()
                        .all()
                    )
                    for entry in retained:
                        if as_utc(entry.expires_at) > now:
                            desired_targets[entry.cidr] = TargetCIDR(
                                entry.cidr,
                                t.id,
                                f"Retained from {t.value}",
                                as_utc(entry.expires_at),
                            )
            await session.execute(
                delete(ResolvedIP)
                .where(ResolvedIP.expires_at <= now)
                .execution_options(synchronize_session=False)
            )
            # Purge cache entries that expired beyond the sliding window
            await self.cache.purge_expired(now)
            await session.commit()

        # Drivers reconcile the complete policy, including enabled and output settings.
        # Collapse overlaps and adjacent prefixes so interval-set backends receive disjoint ranges.
        networks = [ipaddress.ip_network(value, strict=False) for value in desired_targets]
        ipv4 = [net for net in networks if isinstance(net, ipaddress.IPv4Network)]
        ipv6 = [net for net in networks if isinstance(net, ipaddress.IPv6Network)]
        collapsed = [*ipaddress.collapse_addresses(ipv4), *ipaddress.collapse_addresses(ipv6)]
        policy = {TargetCIDR(str(net), "aggregate") for net in collapsed}
        sync_res = await driver.apply_policy(policy, bool(policy))
        await driver.health_check()
        await self._record_result(sync_res, now)
        return sync_res

    async def _record_result(self, result: SyncResult, now: datetime) -> None:
        async with AsyncSessionLocal() as session:
            session.add(
                SyncEvent(
                    driver_name=result.driver_name,
                    added_count=result.added_count,
                    removed_count=result.removed_count,
                    total_active=result.total_active,
                    duration_ms=result.duration_ms,
                    success=result.success,
                    error_message=result.error_message,
                    executed_at=now,
                )
            )
            await session.commit()
        await notify_transition(result)

    def wake(self) -> None:
        """Re-evaluate the runtime schedule after a settings change."""
        self._wake_event.set()

    async def _loop(self) -> None:
        """Zero pauses automatic cycles; settings can enable them without a restart."""
        first = True
        while not self._stop_event.is_set():
            self._wake_event.clear()
            interval = int(settings.sync_interval_seconds)
            if interval > 0:
                await self.reconcile(force=first)
                first = False
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=community_schedule().wait_seconds(max(interval, 30))
                    if interval > 0
                    else None,
                )

    def start_periodic_sync(self) -> None:
        """Start the background sync worker."""
        if self._background_task is None or self._background_task.done():
            self._stop_event.clear()
            self._background_task = asyncio.create_task(self._loop())
            logger.info("Started periodic reconciliation task (interval: %ds)", self.sync_interval)

    async def stop(self) -> None:
        """Signal and stop the background sync worker."""
        self._stop_event.set()
        if self._background_task and not self._background_task.done():
            self._background_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._background_task
        logger.info("Stopped periodic reconciliation task")
