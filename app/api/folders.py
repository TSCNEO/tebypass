"""Folders (personal lists) management endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Folder, Target
from app.db.session import get_db
from app.models.schemas import FolderCreate, FolderResponse, FolderUpdate

router = APIRouter(prefix="/api/v1/folders", tags=["Folders"])


def _to_response(folder: Folder) -> FolderResponse:
    return FolderResponse(
        id=folder.id,
        name=folder.name,
        color=folder.color,
        targets_count=len(folder.targets) if folder.targets else 0,
        created_at=folder.created_at,
    )


async def _get_folder(db: AsyncSession, folder_id: str) -> Folder:
    query = select(Folder).where(Folder.id == folder_id).options(selectinload(Folder.targets))
    folder = (await db.execute(query)).scalar_one_or_none()
    if folder is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    return folder


async def _ensure_unique_name(db: AsyncSession, name: str, exclude_id: str | None = None) -> None:
    query = select(Folder.id).where(func.lower(Folder.name) == name.strip().lower())
    if exclude_id:
        query = query.where(Folder.id != exclude_id)
    if (await db.execute(query)).first() is not None:
        raise HTTPException(status_code=409, detail="A folder with this name already exists")


@router.get("", response_model=list[FolderResponse])
async def list_folders(db: AsyncSession = Depends(get_db)) -> list[FolderResponse]:
    """List every folder with its number of assigned targets."""
    query = select(Folder).options(selectinload(Folder.targets)).order_by(Folder.name.asc())
    folders = (await db.execute(query)).scalars().all()
    return [_to_response(folder) for folder in folders]


@router.post("", response_model=FolderResponse, status_code=status.HTTP_201_CREATED)
async def create_folder(
    payload: FolderCreate,
    db: AsyncSession = Depends(get_db),
) -> FolderResponse:
    """Create a new folder / personal list."""
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Folder name must not be empty")
    await _ensure_unique_name(db, name)

    folder = Folder(name=name, color=payload.color)
    db.add(folder)
    await db.commit()

    return _to_response(await _get_folder(db, folder.id))


@router.patch("/{folder_id}", response_model=FolderResponse)
async def update_folder(
    folder_id: str,
    payload: FolderUpdate,
    db: AsyncSession = Depends(get_db),
) -> FolderResponse:
    """Rename and/or recolor a folder."""
    folder = await _get_folder(db, folder_id)
    changes = payload.model_dump(exclude_unset=True)

    if "name" in changes and changes["name"] is not None:
        name = str(changes["name"]).strip()
        if not name:
            raise HTTPException(status_code=400, detail="Folder name must not be empty")
        await _ensure_unique_name(db, name, exclude_id=folder_id)
        folder.name = name

    if "color" in changes and changes["color"] is not None:
        folder.color = str(changes["color"])

    await db.commit()
    return _to_response(await _get_folder(db, folder_id))


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a folder; its targets are kept and simply become unassigned."""
    folder = await _get_folder(db, folder_id)

    await db.execute(update(Target).where(Target.folder_id == folder_id).values(folder_id=None))
    await db.delete(folder)
    await db.commit()
