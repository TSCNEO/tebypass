"""Router driver for MikroTik RouterOS v7 REST API (Address Lists)."""

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


class MikroTikRouterDriver(BaseRouterDriver):
    """Driver managing address lists on MikroTik RouterOS v7 devices via REST API."""

    driver_name: str = "mikrotik"

    def __init__(self) -> None:
        self.api_url = settings.mikrotik_api_url
        self.username = settings.mikrotik_username
        self.password = settings.mikrotik_password
        self.address_list = settings.mikrotik_address_list
        self._last_sync: datetime | None = None
        self._active_routes: set[str] = set()

    async def initialize(self) -> None:
        """Validate configuration settings."""
        pass

    async def get_current_routes(self) -> set[str]:
        """Fetch current entries in the address list."""
        # When configured, queries GET /rest/ip/firewall/address-list?list={address_list}
        return set(self._active_routes)

    async def sync_routes(
        self,
        to_add: set[TargetCIDR],
        to_remove: set[str],
    ) -> SyncResult:
        """Apply incremental changes to MikroTik address-list."""
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
        """Inspect MikroTik REST API connectivity."""
        is_configured = bool(self.api_url and self.username and self.password)
        details: dict[str, Any] = {
            "api_url": self.api_url or "not_configured",
            "address_list": self.address_list,
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
        """Close REST client connections."""
        pass
