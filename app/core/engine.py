"""Reconciliation engine coordinating resolution, state diffing, and driver sync."""

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.cache import SlidingWindowCache
from app.core.config import settings
from app.core.resolver import NativeDNSResolver
from app.db.models import ResolvedIP, SyncEvent, Target
from app.db.session import AsyncSessionLocal
from app.drivers.base import BaseRouterDriver, SyncResult, TargetCIDR

logger = logging.getLogger("tebypass.engine")


class ReconciliationEngine:
    """Core engine responsible for calculating diffs and keeping router state synchronized."""

    def __init__(
        self,
        driver: BaseRouterDriver,
        cache: SlidingWindowCache,
        resolver: NativeDNSResolver,
        sync_interval_seconds: int = 300,
    ) -> None:
        self.driver = driver
        self.cache = cache
        self.resolver = resolver
        self.sync_interval = sync_interval_seconds
        self._background_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._is_syncing = asyncio.Lock()

    async def reconcile(self, force: bool = False) -> SyncResult:
        """Execute a complete reconciliation cycle: resolve, diff, and dispatch to driver."""
        async with self._is_syncing:
            start_time = time.perf_counter()
            now = datetime.now(UTC)

            # 1. Fetch enabled targets from DB
            desired_targets: dict[str, TargetCIDR] = {}

            async with AsyncSessionLocal() as session:
                query = select(Target).where(Target.enabled.is_(True))
                result = await session.execute(query)
                targets = result.scalars().all()

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

                # Purge cache entries that expired beyond the sliding window
                await self.cache.purge_expired(now)
                await session.commit()

            # 2. Query router driver for current routes
            current_routes = await self.driver.get_current_routes()

            # 3. Compute differences
            desired_cidrs = set(desired_targets.keys())
            cidrs_to_add = desired_cidrs - current_routes
            cidrs_to_remove = current_routes - desired_cidrs

            to_add_objects = {desired_targets[c] for c in cidrs_to_add}

            logger.info(
                "Reconciliation diff: %d to add, %d to remove, %d desired, %d current",
                len(to_add_objects),
                len(cidrs_to_remove),
                len(desired_cidrs),
                len(current_routes),
            )

            # 4. Apply changes via driver
            sync_res = await self.driver.sync_routes(
                to_add=to_add_objects,
                to_remove=cidrs_to_remove,
            )

            # 5. Persist audit sync event
            async with AsyncSessionLocal() as session:
                sync_event = SyncEvent(
                    driver_name=self.driver.driver_name,
                    added_count=sync_res.added_count,
                    removed_count=sync_res.removed_count,
                    total_active=sync_res.total_active,
                    duration_ms=round((time.perf_counter() - start_time) * 1000.0, 2),
                    success=sync_res.success,
                    error_message=sync_res.error_message,
                    executed_at=now,
                )
                session.add(sync_event)
                await session.commit()

            return sync_res

    async def _loop(self) -> None:
        """Internal periodic background loop."""
        while not self._stop_event.is_set():
            try:
                await self.reconcile()
            except Exception as ex:
                logger.error("Error in scheduled reconciliation: %s", ex, exc_info=True)

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=float(self.sync_interval))

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
