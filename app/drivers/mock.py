"""In-memory mock router driver for testing and development."""

import time
from datetime import UTC, datetime
from typing import Any

from app.drivers.base import (
    BaseRouterDriver,
    DriverHealth,
    DriverStatus,
    SyncResult,
    TargetCIDR,
)


class MockRouterDriver(BaseRouterDriver):
    """In-memory driver maintaining route tables without external dependencies."""

    driver_name: str = "mock"

    def __init__(self) -> None:
        self._routes: dict[str, TargetCIDR] = {}
        self._last_sync: datetime | None = None
        self._is_healthy: bool = True
        self._sync_history: list[SyncResult] = []

    async def initialize(self) -> None:
        """Initialize mock driver state."""
        self._routes.clear()
        self._is_healthy = True

    async def get_current_routes(self, force: bool = False) -> set[str]:
        """Return the set of active CIDRs currently held in memory."""
        return set(self._routes.keys())

    async def sync_routes(
        self,
        to_add: set[TargetCIDR],
        to_remove: set[str],
    ) -> SyncResult:
        """Apply added and removed CIDRs in memory."""
        start = time.perf_counter()

        added_count = 0
        removed_count = 0

        for item in to_add:
            if item.cidr not in self._routes:
                self._routes[item.cidr] = item
                added_count += 1

        for cidr in to_remove:
            if cidr in self._routes:
                del self._routes[cidr]
                removed_count += 1

        duration_ms = (time.perf_counter() - start) * 1000.0
        self._last_sync = datetime.now(UTC)

        result = SyncResult(
            driver_name=self.driver_name,
            added_count=added_count,
            removed_count=removed_count,
            total_active=len(self._routes),
            duration_ms=round(duration_ms, 2),
            success=True,
            error_message=None,
        )
        self._sync_history.append(result)
        return result

    async def health_check(self) -> DriverStatus:
        """Return current status of the mock driver."""
        details: dict[str, Any] = {
            "mode": "in-memory-simulation",
            "sync_count": len(self._sync_history),
            "simulated": True,
        }
        return DriverStatus(
            driver_name=self.driver_name,
            health=DriverHealth.HEALTHY if self._is_healthy else DriverHealth.UNHEALTHY,
            active_routes_count=len(self._routes),
            last_sync=self._last_sync,
            details=details,
        )

    async def close(self) -> None:
        """Clear memory structures."""
        self._routes.clear()
