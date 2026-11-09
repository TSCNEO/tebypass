"""HTTP adapter for core services."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import status_service as service
from app.db.session import get_db
from app.models.schemas import SystemStatusResponse

router = APIRouter(tags=["Status"])


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, str]:
    result = await service.healthz()
    result["scheme"] = request.url.scheme
    return result


@router.get("/api/v1/status", response_model=SystemStatusResponse)
async def get_system_status(db: AsyncSession = Depends(get_db)) -> SystemStatusResponse:
    return await service.get_system_status(db)


@router.get("/api/v1/version")
async def get_version_endpoint() -> dict[str, str | int]:
    return await service.get_version_endpoint()
