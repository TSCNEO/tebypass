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
    "get_driver",
    "get_router_driver",
    "invalidate_driver",
]

_cached_driver: BaseRouterDriver | None = None


def get_driver() -> BaseRouterDriver:
    """Return the process-wide router driver, instantiating it on first use."""
    global _cached_driver
    if _cached_driver is None:
        _cached_driver = get_router_driver()
    return _cached_driver


def invalidate_driver() -> None:
    """Drop the cached driver so the next call rebuilds it from the current settings."""
    global _cached_driver
    _cached_driver = None


def get_router_driver(custom_settings: Settings | None = None) -> BaseRouterDriver:
    """Instantiate and return the configured router driver."""
    cfg = custom_settings or settings
    driver_type = cfg.router_driver.lower().strip()

    if driver_type == "mock":
        return MockRouterDriver()
    elif driver_type == "virtual_gateway":
        return VirtualGatewayDriver(set_name=cfg.gateway_nft_set_name)
    elif driver_type == "unifi":
        return UniFiRouterDriver(cfg)
    elif driver_type == "mikrotik":
        return MikroTikRouterDriver(cfg)
    elif driver_type in {"omada", "opnsense"}:
        from app.drivers.stopliga_adapter import StopligaAdapter

        return StopligaAdapter(cfg)
    else:
        raise ValueError(
            f"Unsupported router driver '{driver_type}'. Valid options: mock, virtual_gateway, unifi, mikrotik, omada, opnsense"
        )
