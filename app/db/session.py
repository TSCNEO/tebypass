"""Database session and connection management."""

from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

if settings.database_url:
    DATABASE_URL = settings.database_url
    if DATABASE_URL.startswith("sqlite+aiosqlite:///"):
        # Handle 4 slashes for absolute path or 3 slashes for relative path
        raw_path = DATABASE_URL.split("sqlite+aiosqlite:///")[-1]
        if raw_path:
            Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
else:
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    DATABASE_URL = f"sqlite+aiosqlite:///{settings.db_path}"

engine: AsyncEngine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
    """Apply performance and concurrency pragmas to SQLite on connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields an active async database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def _apply_lightweight_migrations(conn: AsyncConnection) -> None:
    """Add newly introduced columns to pre-existing SQLite tables.

    ``Base.metadata.create_all`` only creates missing tables, so databases created by
    earlier releases need explicit ``ALTER TABLE`` statements for new columns.
    """
    result = await conn.execute(text("PRAGMA table_info(targets)"))
    target_columns = {row[1] for row in result.fetchall()}
    if target_columns and "folder_id" not in target_columns:
        await conn.execute(
            text(
                "ALTER TABLE targets ADD COLUMN folder_id VARCHAR(36) "
                "REFERENCES folders(id) ON DELETE SET NULL"
            )
        )


async def init_db() -> None:
    """Initialize database tables, apply schema upgrades and load persisted settings."""
    from app.core.settings_store import load_into_settings
    from app.db.models import Base

    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL;"))
        await conn.execute(text("PRAGMA synchronous=NORMAL;"))
        await conn.run_sync(Base.metadata.create_all)
        await _apply_lightweight_migrations(conn)

    # Apply previously persisted runtime settings over the environment defaults
    await load_into_settings()
