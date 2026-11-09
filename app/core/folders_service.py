"""Folders (personal lists) management endpoints."""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.service_errors import ServiceError
from app.db.models import Folder, Target
from app.models.schemas import FolderCreate, FolderResponse, FolderUpdate


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
        raise ServiceError(status_code=404, detail="Folder not found")
    return folder


async def _ensure_unique_name(db: AsyncSession, name: str, exclude_id: str | None = None) -> None:
    query = select(Folder.id).where(func.lower(Folder.name) == name.strip().lower())
    if exclude_id:
        query = query.where(Folder.id != exclude_id)
    if (await db.execute(query)).first() is not None:
        raise ServiceError(status_code=409, detail="A folder with this name already exists")


async def list_folders(db: AsyncSession) -> list[FolderResponse]:
    """List every folder with its number of assigned targets."""
    query = select(Folder).options(selectinload(Folder.targets)).order_by(Folder.name.asc())
    folders = (await db.execute(query)).scalars().all()
    return [_to_response(folder) for folder in folders]


async def create_folder(
    payload: FolderCreate,
    db: AsyncSession,
) -> FolderResponse:
    """Create a new folder / personal list."""
    name = payload.name.strip()
    if not name:
        raise ServiceError(status_code=400, detail="Folder name must not be empty")
    await _ensure_unique_name(db, name)

    folder = Folder(name=name, color=payload.color)
    db.add(folder)
    await db.commit()

    return _to_response(await _get_folder(db, folder.id))


async def update_folder(
    folder_id: str,
    payload: FolderUpdate,
    db: AsyncSession,
) -> FolderResponse:
    """Rename and/or recolor a folder."""
    folder = await _get_folder(db, folder_id)
    changes = payload.model_dump(exclude_unset=True)

    if "name" in changes and changes["name"] is not None:
        name = str(changes["name"]).strip()
        if not name:
            raise ServiceError(status_code=400, detail="Folder name must not be empty")
        await _ensure_unique_name(db, name, exclude_id=folder_id)
        folder.name = name

    if "color" in changes and changes["color"] is not None:
        folder.color = str(changes["color"])

    await db.commit()
    return _to_response(await _get_folder(db, folder_id))


async def delete_folder(
    folder_id: str,
    db: AsyncSession,
) -> None:
    """Delete a folder; its targets are kept and simply become unassigned."""
    folder = await _get_folder(db, folder_id)

    await db.execute(update(Target).where(Target.folder_id == folder_id).values(folder_id=None))
    await db.delete(folder)
    await db.commit()
