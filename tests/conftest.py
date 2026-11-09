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
    from app.db.models import AppSetting, Feed, Folder, ResolvedIP, RuntimeState, SyncEvent, Target
    from app.db.session import AsyncSessionLocal, init_db

    await init_db()
    async with AsyncSessionLocal() as session:
        for model in (RuntimeState, SyncEvent, ResolvedIP, Target, Feed, Folder, AppSetting):
            await session.execute(delete(model))
        await session.commit()


@pytest.fixture
async def db_ready(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[None, None]:
    """Provide a clean database and restore the in-memory settings afterwards."""
    import asyncio
    from unittest.mock import AsyncMock

    import app.core.runtime

    monkeypatch.setattr("app.core.engine.ROUTING_LOCK", asyncio.Lock())
    monkeypatch.setattr(
        "app.core.feeds_sync.download", AsyncMock(side_effect=RuntimeError("offline test feed"))
    )
    monkeypatch.setattr(
        "app.core.resolver.NativeDNSResolver.resolve_fqdn", AsyncMock(return_value=[])
    )
    from app.core.config import settings
    from app.core.settings_store import EDITABLE_KEYS
    from app.drivers import invalidate_driver

    snapshot: dict[str, Any] = {key: getattr(settings, key) for key in EDITABLE_KEYS}
    await _reset_database()
    app.core.runtime.engine_instance = None
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

    from app.core.auth import admin_token
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Authorization": "Bearer " + admin_token()},
    ) as http_client:
        yield http_client
