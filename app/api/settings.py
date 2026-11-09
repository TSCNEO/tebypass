"""Runtime settings and router driver diagnostics endpoints."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.core.settings_store import masked_dump, save_settings
from app.drivers import get_router_driver
from app.drivers.unifi import UniFiRouterDriver
from app.models.schemas import RouterTestResponse, UniFiVPNNetwork

logger = logging.getLogger("tebypass.api.settings")

router = APIRouter(prefix="/api/v1", tags=["Settings"])


@router.get("/settings")
async def read_settings() -> dict[str, Any]:
    """Return every editable setting with secret values masked."""
    return masked_dump()


@router.put("/settings")
async def update_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist a batch of settings in SQLite and hot-apply them."""
    try:
        await save_settings(payload)
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex
    return masked_dump()


@router.get("/settings/unifi/vpn-networks", response_model=list[UniFiVPNNetwork])
async def list_unifi_vpn_networks() -> list[UniFiVPNNetwork]:
    """Query the UniFi controller for available VPN Client networks."""
    driver = UniFiRouterDriver()
    try:
        networks = await driver.get_vpn_networks()
        return [
            UniFiVPNNetwork(
                id=net["id"],
                name=net["name"],
                type=net.get("type", "wireguard-client"),
            )
            for net in networks
        ]
    except Exception as ex:
        logger.warning("Failed to fetch UniFi VPN networks: %s", ex)
        raise HTTPException(
            status_code=502,
            detail=f"Error connecting to UniFi controller: {ex}",
        ) from ex
    finally:
        await driver.close()


@router.post("/settings/test-router", response_model=RouterTestResponse)
async def test_router_driver() -> RouterTestResponse:
    """Build a driver with the current settings and probe its health."""
    driver_name = settings.router_driver
    try:
        driver = get_router_driver()
    except ValueError as ex:
        return RouterTestResponse(
            healthy=False,
            driver=driver_name,
            details={"error": str(ex)},
        )

    try:
        await driver.initialize()
        status_report = await driver.health_check()
    except Exception as ex:  # noqa: BLE001 - surfaced to the UI as diagnostics
        logger.warning("Router driver health probe failed: %s", ex)
        return RouterTestResponse(
            healthy=False,
            driver=driver_name,
            details={"error": str(ex)},
        )
    finally:
        await driver.close()

    return RouterTestResponse(
        healthy=status_report.health.value != "unhealthy",
        driver=status_report.driver_name,
        details={
            "health": status_report.health.value,
            "active_routes_count": status_report.active_routes_count,
            **status_report.details,
        },
    )
