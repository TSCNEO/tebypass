"""Shared pytest fixtures: isolated SQLite database and API test client."""

import os
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete

# Must be set before any application module is imported: the engine is built at import time.
_TEST_DIR = Path(tempfile.mkdtemp(prefix="tebypass-tests-"))
os.environ["TEBYPASS_DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DIR / 'tebypass.db'}"


async def _reset_database() -> None:
    """Remove every row from the mutable tables."""
    from app.db.models import AppSetting, Feed, Folder, ResolvedIP, SyncEvent, Target
    from app.db.session import AsyncSessionLocal, init_db

    await init_db()
    async with AsyncSessionLocal() as session:
        for model in (SyncEvent, ResolvedIP, Target, Feed, Folder, AppSetting):
            await session.execute(delete(model))
        await session.commit()


@pytest.fixture
async def db_ready() -> AsyncGenerator[None, None]:
    """Provide a clean database and restore the in-memory settings afterwards."""
    import app.main
    from app.core.config import settings
    from app.core.settings_store import EDITABLE_KEYS
    from app.drivers import invalidate_driver

    snapshot: dict[str, Any] = {key: getattr(settings, key) for key in EDITABLE_KEYS}
    await _reset_database()
    app.main.engine_instance = None
    invalidate_driver()

    yield

    for key, value in snapshot.items():
        setattr(settings, key, value)
    invalidate_driver()
    await _reset_database()


@pytest.fixture
async def client(db_ready: None) -> AsyncGenerator[Any, None]:
    """Async HTTP client bound to the FastAPI application (no lifespan side effects)."""
    import httpx

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client
