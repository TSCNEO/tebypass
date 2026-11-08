"""Driver factory and registry."""

from app.core.config import Settings, settings
from app.drivers.base import (
    BaseRouterDriver,
    DriverHealth,
    DriverStatus,
    SyncResult,
    TargetCIDR,
)
from app.drivers.mikrotik import MikroTikRouterDriver
from app.drivers.mock import MockRouterDriver
from app.drivers.unifi import UniFiRouterDriver
from app.drivers.virtual_gateway import VirtualGatewayDriver

__all__ = [
    "BaseRouterDriver",
    "DriverHealth",
    "DriverStatus",
    "MikroTikRouterDriver",
    "MockRouterDriver",
    "SyncResult",
    "TargetCIDR",
    "UniFiRouterDriver",
    "VirtualGatewayDriver",
    "get_router_driver",
]


def get_router_driver(custom_settings: Settings | None = None) -> BaseRouterDriver:
    """Instantiate and return the configured router driver."""
    cfg = custom_settings or settings
    driver_type = cfg.router_driver.lower().strip()

    if driver_type == "mock":
        return MockRouterDriver()
    elif driver_type == "virtual_gateway":
        return VirtualGatewayDriver(set_name=cfg.gateway_nft_set_name)
    elif driver_type == "unifi":
        return UniFiRouterDriver()
    elif driver_type == "mikrotik":
        return MikroTikRouterDriver()
    else:
        raise ValueError(
            f"Unsupported router driver '{driver_type}'. Valid options: mock, virtual_gateway, unifi, mikrotik"
        )
