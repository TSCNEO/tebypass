"""HTTP adapter for core services."""

from typing import Any

from fastapi import APIRouter

from app.core import settings_service as service
from app.models.schemas import RouterPurgeResponse, RouterTestResponse, UniFiVPNNetwork

router = APIRouter(prefix="/api/v1", tags=["Settings"])


@router.get("/settings")
async def read_settings() -> dict[str, Any]:
    return await service.read_settings()


@router.put("/settings")
async def update_settings(payload: dict[str, Any]) -> dict[str, Any]:
    return await service.update_settings(payload)


@router.get("/settings/unifi/vpn-networks", response_model=list[UniFiVPNNetwork])
async def list_unifi_vpn_networks() -> list[UniFiVPNNetwork]:
    return await service.list_unifi_vpn_networks()


@router.post("/settings/test-router", response_model=RouterTestResponse)
async def test_router_driver() -> RouterTestResponse:
    return await service.test_router_driver()


@router.post("/settings/router/purge", response_model=RouterPurgeResponse)
async def purge_router_routes() -> RouterPurgeResponse:
    return await service.purge_router_routes()
