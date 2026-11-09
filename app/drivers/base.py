"""Abstract base classes and contracts for router drivers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class DriverHealth(StrEnum):
    """Operational health state of a router driver."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True)
class TargetCIDR:
    """Represents a validated IP prefix target for routing."""

    cidr: str
    target_id: str
    description: str = ""
    expires_at: datetime | None = None


@dataclass(frozen=True)
class SyncResult:
    """Outcome report of a route reconciliation operation."""

    driver_name: str
    added_count: int
    removed_count: int
    total_active: int
    duration_ms: float
    success: bool
    error_message: str | None = None


@dataclass(frozen=True)
class DriverStatus:
    """Current state and telemetry of a router driver."""

    driver_name: str
    health: DriverHealth
    active_routes_count: int
    last_sync: datetime | None = None
    details: dict[str, Any] = field(default_factory=dict)


class BaseRouterDriver(ABC):
    """Abstract interface that all router drivers must implement."""

    driver_name: str = "base"

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize driver resources, network sessions, or sockets."""
        pass

    @abstractmethod
    async def get_current_routes(self, force: bool = False) -> set[str]:
        """Query and return the set of currently configured route CIDRs."""
        pass

    @abstractmethod
    async def sync_routes(
        self,
        to_add: set[TargetCIDR],
        to_remove: set[str],
    ) -> SyncResult:
        """Apply incremental route changes idempotently."""
        pass

    async def apply_policy(self, targets: set[TargetCIDR], enabled: bool) -> SyncResult:
        """Observe, apply the desired policy and verify effective destination membership."""
        current = await self.get_current_routes(force=True)
        desired = {target.cidr for target in targets} if enabled else set()
        result = await self.sync_routes(
            {target for target in targets if target.cidr not in current} if enabled else set(),
            current - desired,
        )
        if result.success and await self.get_current_routes(force=True) != desired:
            raise RuntimeError("Router did not retain the desired routes")
        return result

    @abstractmethod
    async def health_check(self) -> DriverStatus:
        """Perform a connectivity check and return operational status."""
        pass

    def get_status(self) -> DriverStatus:
        """Return operational telemetry from memory without executing remote I/O."""
        return DriverStatus(
            driver_name=self.driver_name,
            health=DriverHealth.DEGRADED,
            active_routes_count=0,
            last_sync=None,
            details={},
        )

    @abstractmethod
    async def close(self) -> None:
        """Release open connections, file descriptors, or background tasks."""
        pass

    async def teardown(self) -> None:
        """Purge managed routes, failing explicitly when unsupported."""
        raise NotImplementedError(f"{self.driver_name} does not support teardown")
