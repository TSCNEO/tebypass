"""Router driver for MikroTik RouterOS v7 REST API (Address Lists)."""

from datetime import datetime
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

    async def get_current_routes(self, force: bool = False) -> set[str]:
        """Fetch current entries in the address list."""
        raise NotImplementedError("MikroTik routing is not implemented")

    async def sync_routes(
        self,
        to_add: set[TargetCIDR],
        to_remove: set[str],
    ) -> SyncResult:
        """Apply incremental changes to MikroTik address-list."""
        return SyncResult(
            driver_name=self.driver_name,
            added_count=0,
            removed_count=0,
            total_active=0,
            duration_ms=0,
            success=False,
            error_message="MikroTik routing is not implemented; use a supported driver",
        )

    async def health_check(self) -> DriverStatus:
        """Inspect MikroTik REST API connectivity."""
        is_configured = bool(self.api_url and self.username and self.password)
        details: dict[str, Any] = {
            "api_url": self.api_url or "not_configured",
            "address_list": self.address_list,
            "configured": is_configured,
        }
        details["implemented"] = False
        health = DriverHealth.UNHEALTHY
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
