"""HTTP adapter for core services."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import routes_service as service
from app.db.session import get_db
from app.models.schemas import (
    BulkTargetImportRequest,
    BulkTargetImportResponse,
    SyncResponse,
    SyncTriggerRequest,
    TargetCreate,
    TargetResolveResponse,
    TargetResponse,
    TargetUpdate,
)

router = APIRouter(prefix="/api/v1", tags=["Routes"])


@router.get("/targets", response_model=list[TargetResponse])
async def list_targets(db: AsyncSession = Depends(get_db)) -> list[TargetResponse]:
    return await service.list_targets(db)


@router.post("/targets", response_model=TargetResponse, status_code=status.HTTP_201_CREATED)
async def create_target(
    payload: TargetCreate, db: AsyncSession = Depends(get_db)
) -> TargetResponse:
    return await service.create_target(payload, db)


@router.patch("/targets/{target_id}", response_model=TargetResponse)
async def update_target(
    target_id: str, payload: TargetUpdate, db: AsyncSession = Depends(get_db)
) -> TargetResponse:
    return await service.update_target(target_id, payload, db)


@router.delete("/targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_target(target_id: str, db: AsyncSession = Depends(get_db)) -> None:
    return await service.delete_target(target_id, db)


@router.post("/targets/bulk", response_model=BulkTargetImportResponse)
async def bulk_import_targets(
    payload: BulkTargetImportRequest, db: AsyncSession = Depends(get_db)
) -> BulkTargetImportResponse:
    return await service.bulk_import_targets(payload, db)


@router.post("/targets/{target_id}/resolve", response_model=TargetResolveResponse)
async def resolve_target_now(
    target_id: str, db: AsyncSession = Depends(get_db)
) -> TargetResolveResponse:
    return await service.resolve_target_now(target_id, db)


@router.get("/routes/active", response_model=list[str])
async def get_active_routes() -> list[str]:
    return await service.get_active_routes()


@router.post("/sync", response_model=SyncResponse)
async def trigger_sync(payload: SyncTriggerRequest | None = None) -> SyncResponse:
    return await service.trigger_sync(payload)
