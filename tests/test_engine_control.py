"""Regressions for effective shutdown and serialized routing writes."""

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.engine import ReconciliationEngine
from app.db.models import SyncEvent
from app.db.session import AsyncSessionLocal
from app.drivers.base import TargetCIDR
from app.drivers.mock import MockRouterDriver


async def test_disabled_routing_reports_and_records_teardown_failure(db_ready: None) -> None:
    settings.routing_enabled = False
    driver = MockRouterDriver()
    await driver.sync_routes({TargetCIDR("198.51.100.7/32", "example")}, set())
    with patch.object(driver, "teardown", AsyncMock(side_effect=RuntimeError("flush rejected"))):
        result = await ReconciliationEngine(driver=driver).reconcile()
    assert not result.success
    assert result.total_active == 1
    assert result.removed_count == 0
    assert await driver.get_current_routes() == {"198.51.100.7/32"}
    async with AsyncSessionLocal() as session:
        event = (await session.execute(select(SyncEvent))).scalar_one()
        assert not event.success
        assert event.error_message == "flush rejected"


async def test_disabled_routing_clears_mock_routes(db_ready: None) -> None:
    settings.routing_enabled = False
    driver = MockRouterDriver()
    await driver.sync_routes({TargetCIDR("198.51.100.7/32", "example")}, set())
    result = await ReconciliationEngine(driver=driver).reconcile()
    assert result.success
    assert result.removed_count == 1
    assert not await driver.get_current_routes()


async def test_observation_failure_is_never_reported_as_empty_success(db_ready: None) -> None:
    driver = MockRouterDriver()
    with patch.object(driver, "get_current_routes", AsyncMock(side_effect=RuntimeError("offline"))):
        result = await ReconciliationEngine(driver=driver).reconcile()
    assert not result.success
    assert result.total_active == -1
    assert result.error_message == "offline"


async def test_separate_engines_share_one_writer_lock(db_ready: None) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    driver = MockRouterDriver()

    async def observe(force: bool = False) -> set[str]:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return set()

    with patch.object(driver, "get_current_routes", side_effect=observe):
        first = asyncio.create_task(ReconciliationEngine(driver=driver).reconcile())
        await entered.wait()
        second = asyncio.create_task(ReconciliationEngine(driver=driver).reconcile())
        await asyncio.sleep(0)
        assert calls == 1
        release.set()
        results = await asyncio.gather(first, second)
    assert all(result.success for result in results)
    assert calls == 4


async def test_failed_purge_does_not_claim_success_or_pause(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = MockRouterDriver()
    monkeypatch.setattr("app.api.settings.get_driver", lambda: driver)
    with patch.object(driver, "teardown", AsyncMock(side_effect=RuntimeError("offline"))):
        response = await client.post("/api/v1/settings/router/purge")
    assert response.status_code == 502
    assert settings.routing_enabled
