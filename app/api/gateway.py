"""HTTP adapter for core services."""

from fastapi import APIRouter

from app.core import gateway_service as service
from app.models.schemas import (
    WireGuardStatusResponse,
    WireGuardUploadRequest,
    WireGuardUploadResponse,
)

router = APIRouter(prefix="/api/v1/gateway", tags=["Gateway"])


@router.get("/wireguard", response_model=WireGuardStatusResponse)
async def get_wireguard_config() -> WireGuardStatusResponse:
    return await service.get_wireguard_config()


@router.post("/wireguard", response_model=WireGuardUploadResponse)
async def upload_wireguard_config(payload: WireGuardUploadRequest) -> WireGuardUploadResponse:
    return await service.upload_wireguard_config(payload)


@router.delete("/wireguard", response_model=WireGuardStatusResponse)
async def delete_wireguard_config() -> WireGuardStatusResponse:
    return await service.delete_wireguard_config()
