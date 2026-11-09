"""HTTP adapter for core services."""

from fastapi import APIRouter, status

from app.api.dependencies import DatabaseSession
from app.core import folders_service as service
from app.models.schemas import FolderCreate, FolderResponse, FolderUpdate

router = APIRouter(prefix="/api/v1/folders", tags=["Folders"])


@router.get("", response_model=list[FolderResponse])
async def list_folders(db: DatabaseSession) -> list[FolderResponse]:
    return await service.list_folders(db)


@router.post("", response_model=FolderResponse, status_code=status.HTTP_201_CREATED)
async def create_folder(payload: FolderCreate, db: DatabaseSession) -> FolderResponse:
    return await service.create_folder(payload, db)


@router.patch("/{folder_id}", response_model=FolderResponse)
async def update_folder(
    folder_id: str, payload: FolderUpdate, db: DatabaseSession
) -> FolderResponse:
    return await service.update_folder(folder_id, payload, db)


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(folder_id: str, db: DatabaseSession) -> None:
    return await service.delete_folder(folder_id, db)
