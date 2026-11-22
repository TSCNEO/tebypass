"""Reconciliation engine coordinating resolution, state diffing, and driver sync."""

import asyncio
import contextlib
import ipaddress
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from weakref import WeakKeyDictionary

from app.core.cache import SlidingWindowCache
from app.core.config import settings
from app.core.driver_configuration import driver_identity
from app.core.feeds_sync import refresh_due_feeds
from app.core.notifications import notify_transition
from app.core.policy_ownership import (
    clear_policy_owner,
    clear_unverified_remote_state,
    owns_remote_state,
    read_policy_owner,
    record_policy_owner,
)
from app.core.resolution_store import (
    delete_expired_resolved_ips,
    load_retained_resolved_ips,
    upsert_resolved_ips,
)
from app.core.resolver import DNSLookupResult, NativeDNSResolver, classify_dns_exception
from app.core.schedule import community_schedule
from app.core.targets import as_utc, build_contributing_targets_query
from app.db.models import SyncEvent, Target
from app.db.session import AsyncSessionLocal
from app.drivers import get_driver
from app.drivers.base import BaseRouterDriver, SyncResult, TargetCIDR

logger = logging.getLogger("tebypass.engine")

# All API and periodic engines share one writer lock for settings and router mutations.
ROUTING_LOCK = asyncio.Lock()

_MAX_REACQUIRE_ATTEMPTS = 5
_policy_version = 0
_control_pending = 0
_abort_events: WeakKeyDictionary[asyncio.AbstractEventLoop, set[asyncio.Event]] = (
    WeakKeyDictionary()
)
_control_idle_events: WeakKeyDictionary[asyncio.AbstractEventLoop, set[asyncio.Event]] = (
    WeakKeyDictionary()
)


def current_policy_version() -> int:
    """Return the monotonic configuration/target state version."""
    return _policy_version


def bump_policy_version() -> int:
    """Advance the policy version and wake any in-flight acquisition phase immediately."""
    global _policy_version
    _policy_version += 1
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return _policy_version
    for event in list(_abort_events.get(loop, ())):
        event.set()
    return _policy_version


def _register_abort_event(event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    _abort_events.setdefault(loop, set()).add(event)


def _unregister_abort_event(event: asyncio.Event) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    events = _abort_events.get(loop)
    if events is not None:
        events.discard(event)


def _notify_control_idle() -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for event in list(_control_idle_events.get(loop, ())):
        event.set()


async def _wait_until_control_idle() -> None:
    while _control_pending > 0:
        loop = asyncio.get_running_loop()
        event = asyncio.Event()
        waiters = _control_idle_events.setdefault(loop, set())
        waiters.add(event)
        try:
            if _control_pending > 0:
                await event.wait()
        finally:
            waiters.discard(event)


@contextlib.asynccontextmanager
async def acquire_control_lock() -> AsyncIterator[None]:
    """Acquire ROUTING_LOCK with priority over reconciliation, aborting pending acquisition."""
    global _control_pending
    _control_pending += 1
    bump_policy_version()
    try:
        async with ROUTING_LOCK:
            yield
    finally:
        _control_pending -= 1
        if _control_pending == 0:
            _notify_control_idle()
        bump_policy_version()


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

    def _safe_driver_name(self) -> str:
        if self._explicit_driver is not None:
            return self._explicit_driver.driver_name
        return settings.router_driver

    async def reconcile(self, force: bool = False) -> SyncResult:
        """Acquire outside ROUTING_LOCK, revalidate version, and serialize router writes."""
        try:
            return await self._reconcile(force)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.warning("Reconciliation failed: %s", ex)
            result = SyncResult(
                driver_name=self._safe_driver_name(),
                added_count=0,
                removed_count=0,
                total_active=-1,
                duration_ms=0,
                success=False,
                error_message=str(ex),
            )
            await self._record_result(result, datetime.now(UTC))
            return result

    async def _reconcile(self, force: bool) -> SyncResult:
        """Coordinate acquisition outside ROUTING_LOCK with version-checked router writes."""
        start_time = time.perf_counter()
        attempts = 0
        while True:
            attempts += 1
            now = datetime.now(UTC)

            if not settings.routing_enabled:
                paused_result = await self._reconcile_paused_or_yield(start_time, now)
                if paused_result is not None:
                    return paused_result
                # Routing was re-enabled while waiting; enter normal acquisition.

            start_version = current_policy_version()
            abort_event = asyncio.Event()
            _register_abort_event(abort_event)
            try:
                policy = await self._acquire_policy(
                    force=force,
                    now=now,
                    start_version=start_version,
                    abort_event=abort_event,
                )
            finally:
                _unregister_abort_event(abort_event)

            stale_acquisition = policy is None
            while True:
                await _wait_until_control_idle()
                async with ROUTING_LOCK:
                    if _control_pending > 0:
                        continue
                    if not settings.routing_enabled:
                        return await self._teardown_disabled(self.driver, start_time, now)
                    if stale_acquisition or current_policy_version() != start_version:
                        break
                    assert policy is not None
                    return await self._apply_active_policy(self.driver, policy, start_time, now)

            if attempts >= _MAX_REACQUIRE_ATTEMPTS:
                result = SyncResult(
                    driver_name=self._safe_driver_name(),
                    added_count=0,
                    removed_count=0,
                    total_active=-1,
                    duration_ms=round((time.perf_counter() - start_time) * 1000.0, 2),
                    success=False,
                    error_message="Configuration changed continuously during acquisition",
                )
                await self._record_result(result, now)
                return result

    async def _reconcile_paused_or_yield(
        self, start_time: float, now: datetime
    ) -> SyncResult | None:
        while True:
            await _wait_until_control_idle()
            async with ROUTING_LOCK:
                if _control_pending > 0:
                    continue
                if not settings.routing_enabled:
                    return await self._teardown_disabled(self.driver, start_time, now)
                return None

    async def _teardown_disabled(
        self, driver: BaseRouterDriver, start_time: float, now: datetime
    ) -> SyncResult:
        error = None
        current: set[str] | None = None
        try:
            current = await driver.get_current_routes(force=True)
            await driver.teardown()
            await self._clear_policy_ownership(driver)
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

    async def _resolve_fqdns_bounded(
        self, fqdn_targets: list[Target], abort_event: asyncio.Event
    ) -> dict[str, DNSLookupResult]:
        if not fqdn_targets or abort_event.is_set():
            return {}

        sem = asyncio.Semaphore(max(1, settings.dns_concurrency))
        resolved_by_target: dict[str, DNSLookupResult] = {}

        async def _resolve_one(target: Target) -> None:
            async with sem:
                if abort_event.is_set():
                    return
                try:
                    if hasattr(self.resolver, "resolve_fqdn_with_status"):
                        resolved_by_target[
                            target.id
                        ] = await self.resolver.resolve_fqdn_with_status(
                            fqdn=target.value,
                            window_extension_seconds=settings.sliding_window_seconds,
                        )
                    else:
                        res = await self.resolver.resolve_fqdn(
                            fqdn=target.value,
                            window_extension_seconds=settings.sliding_window_seconds,
                        )
                        resolved_by_target[target.id] = (
                            DNSLookupResult(resolutions=res, status="resolved", error=None)
                            if res
                            else DNSLookupResult(
                                resolutions=[],
                                status="empty",
                                error="DNS returned no usable IP addresses",
                            )
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as ex:  # noqa: BLE001 - isolate individual DNS failures
                    status, detail = classify_dns_exception(ex)
                    logger.warning("DNS resolution failed for '%s': %s", target.value, ex)
                    resolved_by_target[target.id] = DNSLookupResult(
                        resolutions=[], status=status, error=detail
                    )

        tasks = {asyncio.create_task(_resolve_one(t)) for t in fqdn_targets}
        abort_task = asyncio.create_task(abort_event.wait())
        deadline = asyncio.get_running_loop().time() + max(0.05, settings.dns_budget_seconds)
        budget_expired = False

        try:
            while tasks:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    budget_expired = True
                    logger.warning(
                        "DNS resolution budget (%.1fs) reached; resolved %d/%d FQDNs",
                        settings.dns_budget_seconds,
                        len(resolved_by_target),
                        len(fqdn_targets),
                    )
                    break
                wait_set: set[asyncio.Task[Any]] = set(tasks)
                if not abort_task.done():
                    wait_set.add(abort_task)
                done, _ = await asyncio.wait(
                    wait_set,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    budget_expired = True
                    logger.warning(
                        "DNS resolution budget (%.1fs) reached; resolved %d/%d FQDNs",
                        settings.dns_budget_seconds,
                        len(resolved_by_target),
                        len(fqdn_targets),
                    )
                    break
                if abort_task in done:
                    break
                tasks.difference_update(done)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if not abort_task.done():
                abort_task.cancel()
            await asyncio.gather(abort_task, return_exceptions=True)

        if budget_expired and not abort_event.is_set():
            for target in fqdn_targets:
                if target.id not in resolved_by_target:
                    resolved_by_target[target.id] = DNSLookupResult(
                        resolutions=[],
                        status="budget_exceeded",
                        error="DNS resolution budget exceeded before lookup completed",
                    )

        return resolved_by_target

    async def _acquire_policy(
        self,
        *,
        force: bool,
        now: datetime,
        start_version: int,
        abort_event: asyncio.Event,
    ) -> set[TargetCIDR] | None:
        # 0. Refresh any remote feed that is due outside ROUTING_LOCK.
        async with AsyncSessionLocal() as feed_session:
            try:
                await refresh_due_feeds(
                    feed_session,
                    settings.sync_interval_seconds,
                    force=force,
                    abort_event=abort_event,
                )
            except asyncio.CancelledError:
                raise
            except Exception as ex:  # noqa: BLE001 - never abort a sync because of a feed
                logger.warning("Feed refresh failed: %s", ex)

        if (
            abort_event.is_set()
            or not settings.routing_enabled
            or current_policy_version() != start_version
        ):
            return None

        # 1. Fetch enabled targets from DB (a disabled feed contributes nothing)
        desired_targets: dict[str, TargetCIDR] = {}
        async with AsyncSessionLocal() as session:
            rows = await session.execute(build_contributing_targets_query())
            targets = list(rows.scalars().all())

        fqdn_targets: list[Target] = []
        for t in targets:
            if t.destination_type == "cidr":
                cidr_val = t.value.strip()
                desired_targets[cidr_val] = TargetCIDR(
                    cidr=cidr_val,
                    target_id=t.id,
                    description=t.description or f"Direct CIDR target {t.value}",
                )
            elif t.destination_type == "fqdn":
                fqdn_targets.append(t)

        # Resolve FQDNs concurrently with bounded concurrency and total budget.
        resolved_by_target = await self._resolve_fqdns_bounded(fqdn_targets, abort_event)

        if (
            abort_event.is_set()
            or not settings.routing_enabled
            or current_policy_version() != start_version
        ):
            return None

        async with AsyncSessionLocal() as session:
            active_fqdn_targets: dict[str, Target] = {}
            for t in fqdn_targets:
                outcome = resolved_by_target.get(t.id)
                resolutions = outcome.resolutions if outcome is not None else []
                if not await upsert_resolved_ips(session, t.id, resolutions, now):
                    continue
                if outcome is not None:
                    db_target = await session.get(Target, t.id)
                    if db_target is not None:
                        db_target.dns_status = outcome.status
                        db_target.dns_error = outcome.error
                        db_target.dns_last_checked_at = now
                        if resolutions:
                            db_target.dns_last_resolved_at = now
                active_fqdn_targets[t.id] = t
                for r in resolutions:
                    desired_targets[r.cidr] = TargetCIDR(
                        cidr=r.cidr,
                        target_id=t.id,
                        description=f"Resolved from {t.value}",
                        expires_at=r.expires_at,
                    )

            await delete_expired_resolved_ips(session, now)

            # Persisted retention survives rotation, transient DNS failure and restart.
            retained_by_target = await load_retained_resolved_ips(
                session, set(active_fqdn_targets), now
            )
            for target_id, retained in retained_by_target.items():
                t = active_fqdn_targets[target_id]
                for entry in retained:
                    desired_targets[entry.cidr] = TargetCIDR(
                        entry.cidr,
                        t.id,
                        f"Retained from {t.value}",
                        as_utc(entry.expires_at),
                    )

            await self.cache.purge_expired(now)
            await session.commit()

        if (
            abort_event.is_set()
            or not settings.routing_enabled
            or current_policy_version() != start_version
        ):
            return None

        # Collapse overlaps and adjacent prefixes so interval-set backends receive disjoint ranges.
        networks = [ipaddress.ip_network(value, strict=False) for value in desired_targets]
        ipv4 = [net for net in networks if isinstance(net, ipaddress.IPv4Network)]
        ipv6 = [net for net in networks if isinstance(net, ipaddress.IPv6Network)]
        collapsed = [*ipaddress.collapse_addresses(ipv4), *ipaddress.collapse_addresses(ipv6)]
        return {TargetCIDR(str(net), "aggregate") for net in collapsed}

    async def _apply_active_policy(
        self,
        driver: BaseRouterDriver,
        policy: set[TargetCIDR],
        start_time: float,
        now: datetime,
    ) -> SyncResult:
        driver.policy_write_started = False
        try:
            sync_res = await driver.apply_policy(policy, bool(policy))
        except asyncio.CancelledError:
            if driver.policy_write_started:
                await asyncio.shield(self._track_policy_ownership(driver, None))
            raise
        except Exception:
            await self._track_policy_ownership(driver, None)
            raise
        await self._track_policy_ownership(driver, sync_res)
        if not (sync_res.success and driver.health_observed_during_apply):
            await driver.health_check()
        await self._record_result(sync_res, now)
        return sync_res

    async def _track_policy_ownership(
        self,
        driver: BaseRouterDriver,
        result: SyncResult | None,
    ) -> None:
        """Persist whether the current configuration may own a remote policy.

        ``result is None`` means the driver raised. A failure only records ownership when
        the driver reached a write boundary; otherwise an existing record is preserved
        because a failed cycle does not prove that remote state is empty.
        """
        if self._explicit_driver is not None:
            return
        if (
            not owns_remote_state(driver.driver_name)
            or driver.driver_name != settings.router_driver
        ):
            return
        try:
            if result is not None and result.success:
                if result.total_active > 0:
                    await record_policy_owner(settings)
                elif driver.retains_policy_when_disabled:
                    existing = await read_policy_owner()
                    if driver.policy_write_started or (
                        existing is not None and existing.identity == driver_identity(settings)
                    ):
                        await record_policy_owner(settings)
                    else:
                        await clear_policy_owner()
                else:
                    await clear_policy_owner()
                await clear_unverified_remote_state()
            elif driver.policy_write_started:
                await record_policy_owner(settings, possible_partial=True)
        except Exception as ex:  # noqa: BLE001 - ownership tracking must not break sync
            logger.warning("Router policy ownership could not be persisted: %s", ex)

    async def _clear_policy_ownership(self, driver: BaseRouterDriver) -> None:
        """Clear ownership after a verified teardown of the configured remote driver."""
        if self._explicit_driver is not None:
            return
        if (
            not owns_remote_state(driver.driver_name)
            or driver.driver_name != settings.router_driver
        ):
            return
        try:
            await clear_policy_owner()
            await clear_unverified_remote_state()
        except Exception as ex:  # noqa: BLE001 - teardown already verified the remote state
            logger.warning("Router policy ownership could not be cleared: %s", ex)

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
