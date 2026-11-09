"""HTTP adapter for core services."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import folders_service as service
from app.db.session import get_db
from app.models.schemas import FolderCreate, FolderResponse, FolderUpdate

router = APIRouter(prefix="/api/v1/folders", tags=["Folders"])


@router.get("", response_model=list[FolderResponse])
async def list_folders(db: AsyncSession = Depends(get_db)) -> list[FolderResponse]:
    return await service.list_folders(db)


@router.post("", response_model=FolderResponse, status_code=status.HTTP_201_CREATED)
async def create_folder(
    payload: FolderCreate, db: AsyncSession = Depends(get_db)
) -> FolderResponse:
    return await service.create_folder(payload, db)


@router.patch("/{folder_id}", response_model=FolderResponse)
async def update_folder(
    folder_id: str, payload: FolderUpdate, db: AsyncSession = Depends(get_db)
) -> FolderResponse:
    return await service.update_folder(folder_id, payload, db)


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(folder_id: str, db: AsyncSession = Depends(get_db)) -> None:
    return await service.delete_folder(folder_id, db)
