"""Regression tests for audit findings C16 (task supervisor) and R01 (UniFi kill-switch)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from app.core import runtime
from app.core.config import Settings, settings
from app.db.models import Feed
from app.db.session import AsyncSessionLocal
from app.drivers.base import DriverHealth, SyncResult, TargetCIDR
from app.drivers.unifi import UniFiRouterDriver

# ---------------------------------------------------------------------------
# C16: Background task supervisor, coalescing, error logging, shutdown drain
# ---------------------------------------------------------------------------


async def test_c16_track_background_task_retains_and_logs_unhandled_error(
    caplog: pytest.LogCaptureFixture, db_ready: None
) -> None:
    async def failing() -> None:
        raise RuntimeError("boom-in-background")

    with caplog.at_level(logging.ERROR, logger="tebypass.runtime"):
        task = runtime.track_background_task(failing(), name="test-failing-task")
        assert task in runtime._BACKGROUND_TASKS
        with pytest.raises(RuntimeError, match="boom-in-background"):
            await task
        # Let done callback run on the event loop
        await asyncio.sleep(0)

    assert task not in runtime._BACKGROUND_TASKS
    assert any("test-failing-task" in rec.message for rec in caplog.records)


async def test_c16_schedule_reconciliation_coalesces_bursts(
    monkeypatch: pytest.MonkeyPatch, db_ready: None
) -> None:
    calls: list[bool] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_trigger(force: bool = True) -> SyncResult:
        calls.append(force)
        if len(calls) == 1:
            started.set()
            await release.wait()
        return SyncResult("mock", 0, 0, 0, 1.0, True)

    monkeypatch.setattr(runtime, "trigger_reconciliation", fake_trigger)

    first = runtime.schedule_reconciliation(force=False)
    await started.wait()
    # Burst 3 more triggers while the first one is still running: should coalesce into 1 follow-up
    second = runtime.schedule_reconciliation(force=False)
    third = runtime.schedule_reconciliation(force=True)
    fourth = runtime.schedule_reconciliation(force=False)
    assert second is first and third is first and fourth is first

    release.set()
    await first
    assert calls == [False, True]


async def test_c16_drain_background_tasks_cancels_stragglers(db_ready: None) -> None:
    cancelled = asyncio.Event()

    async def sleepy() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = runtime.track_background_task(sleepy(), name="test-straggler")
    await asyncio.sleep(0)
    assert not task.done()

    await runtime.drain_background_tasks(timeout=0.05)
    assert cancelled.is_set()
    assert task.done()
    assert task not in runtime._BACKGROUND_TASKS


async def test_c16_feed_and_settings_endpoints_use_schedule_reconciliation(
    client: httpx.AsyncClient,
) -> None:
    async with AsyncSessionLocal() as db:
        feed = Feed(
            name="TrackedFeed",
            url="https://feed.example.org/data.json",
            format="hayahora",
            enabled=False,
        )
        db.add(feed)
        await db.commit()
        feed_id = feed.id

    with patch("app.core.runtime.schedule_reconciliation") as sched_mock:
        toggled = await client.post(f"/api/v1/feeds/{feed_id}/toggle")
        assert toggled.status_code == 200
        assert sched_mock.call_count == 1

        cleared = await client.post(f"/api/v1/feeds/{feed_id}/clear", json={})
        assert cleared.status_code == 200
        assert sched_mock.call_count == 2

        updated = await client.put(
            "/api/v1/settings",
            json={"sync_interval_seconds": 120},
        )
        assert updated.status_code == 200
        assert sched_mock.call_count == 3


# ---------------------------------------------------------------------------
# R01: UniFi kill_switch_enabled setting & diagnostic visibility
# ---------------------------------------------------------------------------


async def test_r01_unifi_kill_switch_defaults_false_and_persists(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get("/api/v1/settings")
    assert resp.status_code == 200
    assert resp.json()["unifi_kill_switch"] is False

    updated = await client.put(
        "/api/v1/settings",
        json={"unifi_kill_switch": True, "skip_auto_sync": True},
    )
    assert updated.status_code == 200
    assert updated.json()["unifi_kill_switch"] is True
    assert settings.unifi_kill_switch is True


async def test_r01_unifi_driver_applies_and_exposes_kill_switch_state() -> None:
    routes: list[dict[str, Any]] = []
    sent_payloads: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/api/self/sites" in url:
            return httpx.Response(200, json={"data": [{"name": "default"}]})
        if "/trafficroutes" in url and request.method == "GET":
            return httpx.Response(200, json=routes)
        if "/trafficroutes" in url and request.method == "POST":
            body = json.loads(request.content)
            sent_payloads.append(body)
            created = {**body, "_id": "r-1"}
            routes.clear()
            routes.append(created)
            return httpx.Response(201, json=created)
        if "/trafficroutes/" in url and request.method == "PUT":
            body = json.loads(request.content)
            sent_payloads.append(body)
            routes[0] = body
            return httpx.Response(200, json=body)
        if "/rest/networkconf" in url:
            return httpx.Response(
                200,
                json={"data": [{"_id": "vpn-1", "name": "WG", "purpose": "vpn-client"}]},
            )
        return httpx.Response(404)

    driver_off = UniFiRouterDriver(
        Settings(
            router_driver="unifi",
            unifi_controller_url="https://198.51.100.1",
            unifi_api_key="key",
            unifi_vpn_network_id="vpn-1",
            unifi_kill_switch=False,
        )
    )
    driver_off._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    res_off = await driver_off.apply_policy({TargetCIDR("198.51.100.7/32", "t1")}, True)
    assert res_off.success is True
    assert sent_payloads[-1]["kill_switch_enabled"] is False
    probe_off = await driver_off.health_check()
    assert probe_off.health == DriverHealth.HEALTHY
    assert probe_off.details["kill_switch_enabled"] is False

    # Enable kill switch explicitly -> PUT updates kill_switch_enabled=True and status reflects it
    driver_on = UniFiRouterDriver(
        Settings(
            router_driver="unifi",
            unifi_controller_url="https://198.51.100.1",
            unifi_api_key="key",
            unifi_vpn_network_id="vpn-1",
            unifi_kill_switch=True,
        )
    )
    driver_on._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    res_on = await driver_on.apply_policy({TargetCIDR("198.51.100.7/32", "t1")}, True)
    assert res_on.success is True
    assert sent_payloads[-1]["kill_switch_enabled"] is True
    probe_on = await driver_on.health_check()
    assert probe_on.details["kill_switch_enabled"] is True


async def test_r01_ui_template_exposes_kill_switch_checkbox(client: httpx.AsyncClient) -> None:
    resp = await client.get("/")
    assert resp.status_code == 200
    assert 'id="unifi-kill-switch"' in resp.text
    assert 'aria-describedby="unifi-kill-switch-hint"' in resp.text
    assert "kill_switch_enabled" in resp.text
