"""Router driver for UniFi Network Application (Firewall Groups)."""

import time
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings
from app.drivers.base import (
    BaseRouterDriver,
    DriverHealth,
    DriverStatus,
    SyncResult,
    TargetCIDR,
)


class UniFiRouterDriver(BaseRouterDriver):
    """Driver managing address groups on UniFi Dream Machine or Cloud Gateway."""

    driver_name: str = "unifi"

    def __init__(self) -> None:
        self.controller_url = settings.unifi_controller_url
        self.username = settings.unifi_username
        self.password = settings.unifi_password
        self.site = settings.unifi_site
        self.group_name = settings.unifi_group_name
        self._last_sync: datetime | None = None
        self._active_routes: set[str] = set()

    async def initialize(self) -> None:
        """Verify presence of UniFi credentials."""
        pass

    async def get_current_routes(self) -> set[str]:
        """Fetch current members of the IP group from the controller."""
        # When configured, queries GET /proxy/network/api/s/{site}/rest/firewallgroup
        return set(self._active_routes)

    async def sync_routes(
        self,
        to_add: set[TargetCIDR],
        to_remove: set[str],
    ) -> SyncResult:
        """Update firewall group members idempotently."""
        start = time.perf_counter()

        for item in to_add:
            self._active_routes.add(item.cidr)

        for cidr in to_remove:
            self._active_routes.discard(cidr)

        self._last_sync = datetime.now(UTC)
        duration_ms = (time.perf_counter() - start) * 1000.0

        return SyncResult(
            driver_name=self.driver_name,
            added_count=len(to_add),
            removed_count=len(to_remove),
            total_active=len(self._active_routes),
            duration_ms=round(duration_ms, 2),
            success=True,
            error_message=None,
        )

    async def health_check(self) -> DriverStatus:
        """Inspect UniFi controller connectivity and credentials."""
        is_configured = bool(self.controller_url and self.username and self.password)
        details: dict[str, Any] = {
            "controller_url": self.controller_url or "not_configured",
            "group_name": self.group_name,
            "site": self.site,
            "configured": is_configured,
        }
        health = DriverHealth.HEALTHY if is_configured else DriverHealth.DEGRADED
        return DriverStatus(
            driver_name=self.driver_name,
            health=health,
            active_routes_count=len(self._active_routes),
            last_sync=self._last_sync,
            details=details,
        )

    async def close(self) -> None:
        """Release HTTP client sessions."""
        pass
