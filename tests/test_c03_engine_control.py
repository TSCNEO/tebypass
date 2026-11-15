"""Regression tests for C03: bounded DNS/feed acquisition, control priority, and safe cancellation."""

import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select

from app.core.cache import CachedResolution
from app.core.config import settings
from app.core.engine import ReconciliationEngine, acquire_control_lock
from app.core.policy_ownership import read_policy_owner
from app.db.models import Feed, ResolvedIP, Target
from app.db.session import AsyncSessionLocal
from app.drivers.base import BaseRouterDriver, DriverHealth, DriverStatus, SyncResult, TargetCIDR
from app.drivers.stopliga_adapter import run_blocking


class _RecordingDriver(BaseRouterDriver):
    driver_name = "mock"
    retains_policy_when_disabled = False

    def __init__(self) -> None:
        self.applied_policies: list[set[str]] = []
        self.teardown_calls = 0
        self.active_writes = 0
        self.max_concurrent_writes = 0
        self.routes: set[str] = set()
        self.write_entered = asyncio.Event()
        self.release_write: asyncio.Event | None = None

    async def initialize(self) -> None:
        return None

    async def get_current_routes(self, force: bool = False) -> set[str]:
        return set(self.routes)

    async def apply_policy(self, targets: set[TargetCIDR], enabled: bool) -> SyncResult:
        self.active_writes += 1
        self.max_concurrent_writes = max(self.max_concurrent_writes, self.active_writes)
        self.policy_write_started = True
        self.write_entered.set()
        try:
            if self.release_write is not None:
                await self.release_write.wait()
            cidrs = {t.cidr for t in targets} if enabled else set()
            self.applied_policies.append(cidrs)
            self.routes = set(cidrs)
            return SyncResult(
                driver_name=self.driver_name,
                added_count=len(cidrs),
                removed_count=0,
                total_active=len(cidrs),
                duration_ms=1.0,
                success=True,
            )
        finally:
            self.active_writes -= 1

    async def sync_routes(self, to_add: set[TargetCIDR], to_remove: set[str]) -> SyncResult:
        return await self.apply_policy(to_add, True)

    async def teardown(self) -> None:
        self.active_writes += 1
        self.max_concurrent_writes = max(self.max_concurrent_writes, self.active_writes)
        try:
            self.teardown_calls += 1
            self.routes.clear()
        finally:
            self.active_writes -= 1

    async def health_check(self) -> DriverStatus:
        return DriverStatus(
            driver_name=self.driver_name,
            health=DriverHealth.HEALTHY,
            active_routes_count=len(self.routes),
        )

    async def close(self) -> None:
        return None


async def _seed_fqdn(value: str = "slow.example.test") -> str:
    async with AsyncSessionLocal() as session:
        target = Target(value=value, destination_type="fqdn", enabled=True)
        session.add(target)
        await session.commit()
        return target.id


async def test_pause_and_purge_preempt_slow_dns_without_reapplying_stale_policy(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_fqdn("slow-dns.example.test")
    driver = _RecordingDriver()
    engine = ReconciliationEngine(driver=driver)
    monkeypatch.setattr("app.core.runtime.engine_instance", engine)
    monkeypatch.setattr("app.core.settings_service.get_driver", lambda: driver)

    dns_entered = asyncio.Event()
    release_dns = asyncio.Event()

    async def slow_resolve(fqdn: str, window_extension_seconds: int = 0) -> list[CachedResolution]:
        dns_entered.set()
        await release_dns.wait()
        now = datetime.now(UTC)
        return [
            CachedResolution(
                fqdn=fqdn,
                ip_address="198.51.100.10",
                cidr="198.51.100.10/32",
                first_seen_at=now,
                last_seen_at=now,
                expires_at=now + timedelta(seconds=60),
                dns_ttl=60,
            )
        ]

    with patch.object(engine.resolver, "resolve_fqdn", side_effect=slow_resolve):
        sync_task = asyncio.create_task(engine.reconcile())
        await asyncio.wait_for(dns_entered.wait(), timeout=1.0)

        t0 = time.perf_counter()
        pause_resp = await client.put("/api/v1/settings", json={"routing_enabled": False})
        pause_elapsed = time.perf_counter() - t0
        assert pause_resp.status_code == 200
        assert pause_elapsed < 0.5
        assert settings.routing_enabled is False

        t1 = time.perf_counter()
        purge_resp = await client.post("/api/v1/settings/router/purge")
        purge_elapsed = time.perf_counter() - t1
        assert purge_resp.status_code == 200
        assert purge_elapsed < 0.5

        release_dns.set()
        result = await asyncio.wait_for(sync_task, timeout=1.0)

    assert result.success
    assert result.total_active == 0
    assert driver.applied_policies == []
    assert driver.routes == set()


async def test_slow_feed_and_manual_fetch_do_not_block_pause_or_wireguard(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "wireguard_config_path", str(tmp_path / "wg0.conf"))
    async with AsyncSessionLocal() as session:
        feed = Feed(
            name="Slow Feed",
            url="https://example.invalid/feed.txt",
            format="plaintext",
            enabled=True,
        )
        session.add(feed)
        await session.commit()
        feed_id = feed.id

    feed_entered = asyncio.Event()
    release_feed = asyncio.Event()

    async def slow_download(url: str, etag: str | None = None) -> tuple[str | None, str | None]:
        feed_entered.set()
        await release_feed.wait()
        return ("198.51.100.20/32\n", '"v1"')

    monkeypatch.setattr("app.core.feeds_sync.download", slow_download)
    engine = ReconciliationEngine(driver=_RecordingDriver())
    monkeypatch.setattr("app.core.runtime.engine_instance", engine)

    # 1) Background reconcile stalled on feed download does not block WireGuard upload or pause
    sync_task = asyncio.create_task(engine.reconcile(force=True))
    await asyncio.wait_for(feed_entered.wait(), timeout=1.0)

    wg_conf = (
        "[Interface]\n"
        "PrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\n"
        "Address = 192.0.2.2/32\n"
        "[Peer]\n"
        "PublicKey = BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=\n"
        "Endpoint = 203.0.113.1:51820\n"
        "AllowedIPs = 0.0.0.0/0\n"
    )
    t0 = time.perf_counter()
    wg_resp = await client.post("/api/v1/gateway/wireguard", json={"content": wg_conf})
    assert wg_resp.status_code == 200
    assert (time.perf_counter() - t0) < 0.5

    t1 = time.perf_counter()
    pause_resp = await client.put("/api/v1/settings", json={"routing_enabled": False})
    assert pause_resp.status_code == 200
    assert (time.perf_counter() - t1) < 0.5

    release_feed.set()
    await asyncio.wait_for(sync_task, timeout=1.0)

    # 2) Manual fetch_feed_now stalled on download also does not block pause/settings
    settings.routing_enabled = True
    feed_entered.clear()
    release_feed.clear()
    fetch_task = asyncio.create_task(client.post(f"/api/v1/feeds/{feed_id}/fetch"))
    await asyncio.wait_for(feed_entered.wait(), timeout=1.0)

    t2 = time.perf_counter()
    pause_resp_2 = await client.put("/api/v1/settings", json={"routing_enabled": False})
    assert pause_resp_2.status_code == 200
    assert (time.perf_counter() - t2) < 0.5

    release_feed.set()
    fetch_resp = await asyncio.wait_for(fetch_task, timeout=1.0)
    assert fetch_resp.status_code == 200


async def test_pause_then_rapid_resume_discards_stale_pre_pause_policy(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_id = await _seed_fqdn("old.example.test")
    driver = _RecordingDriver()
    engine = ReconciliationEngine(driver=driver)
    monkeypatch.setattr("app.core.runtime.engine_instance", engine)

    dns_entered = asyncio.Event()
    release_old = asyncio.Event()

    async def selective_resolve(
        fqdn: str, window_extension_seconds: int = 0
    ) -> list[CachedResolution]:
        if fqdn == "old.example.test":
            dns_entered.set()
            await release_old.wait()
            now = datetime.now(UTC)
            return [
                CachedResolution(
                    fqdn=fqdn,
                    ip_address="198.51.100.30",
                    cidr="198.51.100.30/32",
                    first_seen_at=now,
                    last_seen_at=now,
                    expires_at=now + timedelta(seconds=60),
                    dns_ttl=60,
                )
            ]
        return []

    with patch.object(engine.resolver, "resolve_fqdn", side_effect=selective_resolve):
        sync_task = asyncio.create_task(engine.reconcile())
        await asyncio.wait_for(dns_entered.wait(), timeout=1.0)

        # Pause, replace target with a CIDR, and quickly resume while Phase 1 is mid-flight.
        assert (
            await client.put("/api/v1/settings", json={"routing_enabled": False})
        ).status_code == 200
        assert (await client.delete(f"/api/v1/targets/{target_id}")).status_code == 204
        assert (
            await client.post(
                "/api/v1/targets",
                json={"value": "203.0.113.44/32", "destination_type": "cidr"},
            )
        ).status_code == 201
        assert (
            await client.put("/api/v1/settings", json={"routing_enabled": True})
        ).status_code == 200

        release_old.set()
        result = await asyncio.wait_for(sync_task, timeout=1.0)
        final_result = await engine.reconcile()

    assert result.success
    assert final_result.success
    assert all("198.51.100.30/32" not in policy for policy in driver.applied_policies)
    assert driver.routes == {"203.0.113.44/32"}


async def test_already_computed_pre_pause_policy_is_discarded_before_write(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _RecordingDriver()
    engine = ReconciliationEngine(driver=driver)
    monkeypatch.setattr("app.core.runtime.engine_instance", engine)

    acquired_stale = asyncio.Event()
    release_phase2 = asyncio.Event()
    calls = 0
    original_acquire = engine._acquire_policy

    async def scripted_acquire(**kwargs: object) -> set[TargetCIDR] | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            acquired_stale.set()
            await release_phase2.wait()
            # Simulate Phase 1 returning a computed policy right before version check.
            return {TargetCIDR("198.51.100.31/32", "stale")}
        return await original_acquire(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(engine, "_acquire_policy", scripted_acquire)
    sync_task = asyncio.create_task(engine.reconcile())
    await asyncio.wait_for(acquired_stale.wait(), timeout=1.0)

    # Pause and quickly resume while Phase 1 already computed {198.51.100.31/32}.
    assert (
        await client.put("/api/v1/settings", json={"routing_enabled": False})
    ).status_code == 200
    assert (
        await client.post(
            "/api/v1/targets",
            json={"value": "203.0.113.45/32", "destination_type": "cidr"},
        )
    ).status_code == 201
    assert (await client.put("/api/v1/settings", json={"routing_enabled": True})).status_code == 200

    release_phase2.set()
    result = await asyncio.wait_for(sync_task, timeout=1.0)
    assert result.success
    assert all("198.51.100.31/32" not in policy for policy in driver.applied_policies)
    assert driver.routes == {"203.0.113.45/32"}


async def test_control_lock_prioritizes_pause_ahead_of_queued_reconcile_without_overlap(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with AsyncSessionLocal() as session:
        session.add(Target(value="198.51.100.50/32", destination_type="cidr", enabled=True))
        await session.commit()

    driver = _RecordingDriver()
    driver.release_write = asyncio.Event()
    engine = ReconciliationEngine(driver=driver)
    monkeypatch.setattr("app.core.runtime.engine_instance", engine)

    first_task = asyncio.create_task(engine.reconcile())
    await asyncio.wait_for(driver.write_entered.wait(), timeout=1.0)

    # Queue a second reconcile while the first holds ROUTING_LOCK in apply_policy.
    second_task = asyncio.create_task(engine.reconcile())
    await asyncio.sleep(0.02)

    # Now request a pause; it must preempt the queued second reconcile once the active write ends.
    pause_task = asyncio.create_task(
        client.put("/api/v1/settings", json={"routing_enabled": False})
    )
    await asyncio.sleep(0.02)

    driver.release_write.set()
    pause_resp, first_res, second_res = await asyncio.gather(pause_task, first_task, second_task)

    assert pause_resp.status_code == 200
    assert first_res.success
    assert second_res.success
    assert driver.max_concurrent_writes == 1
    assert len(driver.applied_policies) == 1
    assert driver.teardown_calls >= 1
    assert driver.routes == set()


async def test_bounded_dns_concurrency_and_budget_retains_existing_ips(
    db_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "dns_concurrency", 2)
    monkeypatch.setattr(settings, "dns_budget_seconds", 0.25)

    now = datetime.now(UTC)
    target_ids: list[str] = []
    async with AsyncSessionLocal() as session:
        for i in range(6):
            target = Target(value=f"host{i}.example.test", destination_type="fqdn", enabled=True)
            session.add(target)
            await session.flush()
            target_ids.append(target.id)
        session.add(
            ResolvedIP(
                target_id=target_ids[0],
                ip_address="198.51.100.77",
                cidr="198.51.100.77/32",
                ttl_seconds=600,
                first_seen_at=now - timedelta(seconds=30),
                last_seen_at=now - timedelta(seconds=30),
                expires_at=now + timedelta(seconds=300),
            )
        )
        await session.commit()

    in_flight = 0
    max_in_flight = 0

    async def bounded_slow_resolve(
        fqdn: str, window_extension_seconds: int = 0
    ) -> list[CachedResolution]:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        try:
            await asyncio.sleep(1.5)
            return []
        finally:
            in_flight -= 1

    driver = _RecordingDriver()
    engine = ReconciliationEngine(driver=driver)
    t0 = time.perf_counter()
    with patch.object(engine.resolver, "resolve_fqdn", side_effect=bounded_slow_resolve):
        result = await engine.reconcile()
    elapsed = time.perf_counter() - t0

    assert result.success
    assert max_in_flight <= 2
    assert elapsed < 1.0
    assert "198.51.100.77/32" in driver.routes


async def test_bounded_feed_concurrency_and_budget(
    db_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "feed_concurrency", 2)
    monkeypatch.setattr(settings, "feed_budget_seconds", 0.25)

    async with AsyncSessionLocal() as session:
        for i in range(5):
            session.add(
                Feed(
                    name=f"Feed {i}",
                    url=f"https://example.invalid/feed-{i}.txt",
                    format="plaintext",
                    enabled=True,
                )
            )
        await session.commit()

    in_flight = 0
    max_in_flight = 0

    async def slow_download(url: str, etag: str | None = None) -> tuple[str | None, str | None]:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        try:
            await asyncio.sleep(1.5)
            return ("198.51.100.88/32\n", '"etag"')
        finally:
            in_flight -= 1

    monkeypatch.setattr("app.core.feeds_sync.download", slow_download)
    engine = ReconciliationEngine(driver=_RecordingDriver())
    t0 = time.perf_counter()
    result = await engine.reconcile(force=True)
    elapsed = time.perf_counter() - t0

    assert result.success
    assert max_in_flight <= 2
    assert elapsed < 1.0


async def test_stop_during_phase1_and_shielded_router_write(
    db_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Part A: engine.stop() cancels Phase 1 immediately.
    await _seed_fqdn("hang.example.test")
    engine = ReconciliationEngine(driver=_RecordingDriver(), sync_interval_seconds=60)
    dns_entered = asyncio.Event()

    async def hanging_resolve(
        fqdn: str, window_extension_seconds: int = 0
    ) -> list[CachedResolution]:
        dns_entered.set()
        await asyncio.sleep(30)
        return []

    with patch.object(engine.resolver, "resolve_fqdn", side_effect=hanging_resolve):
        engine.start_periodic_sync()
        await asyncio.wait_for(dns_entered.wait(), timeout=1.0)
        t0 = time.perf_counter()
        await asyncio.wait_for(engine.stop(), timeout=1.0)
        assert (time.perf_counter() - t0) < 0.5

    # Part B: cancellation mid-router-write awaits shielded thread completion and records partial.
    import threading

    thread_started = threading.Event()
    thread_finished = threading.Event()

    class _ShieldedRemoteDriver(BaseRouterDriver):
        driver_name = "mikrotik"
        retains_policy_when_disabled = True

        async def initialize(self) -> None:
            return None

        async def get_current_routes(self, force: bool = False) -> set[str]:
            return set()

        async def apply_policy(self, targets: set[TargetCIDR], enabled: bool) -> SyncResult:
            self.policy_write_started = True

            def _blocking_write() -> None:
                thread_started.set()
                time.sleep(0.15)
                thread_finished.set()

            await run_blocking(_blocking_write)
            return SyncResult(
                driver_name=self.driver_name,
                added_count=0,
                removed_count=0,
                total_active=0,
                duration_ms=150.0,
                success=True,
            )

        async def sync_routes(self, to_add: set[TargetCIDR], to_remove: set[str]) -> SyncResult:
            return await self.apply_policy(to_add, True)

        async def teardown(self) -> None:
            return None

        async def health_check(self) -> DriverStatus:
            return DriverStatus(
                driver_name=self.driver_name,
                health=DriverHealth.HEALTHY,
                active_routes_count=0,
            )

        async def close(self) -> None:
            return None

    monkeypatch.setattr(settings, "router_driver", "mikrotik")
    monkeypatch.setattr(settings, "mikrotik_api_url", "https://192.0.2.1/rest")
    monkeypatch.setattr(settings, "mikrotik_username", "admin")
    monkeypatch.setattr(settings, "mikrotik_password", "secret")
    monkeypatch.setattr(settings, "mikrotik_routing_table", "vpn")
    shielded_driver = _ShieldedRemoteDriver()
    monkeypatch.setattr("app.core.engine.get_driver", lambda: shielded_driver)

    async with AsyncSessionLocal() as session:
        remaining = (await session.execute(select(Target))).scalars().all()
        for row in remaining:
            await session.delete(row)
        session.add(Target(value="198.51.100.99/32", destination_type="cidr", enabled=True))
        await session.commit()

    engine_b = ReconciliationEngine(driver=None, sync_interval_seconds=60)
    with patch.object(engine_b.resolver, "resolve_fqdn", AsyncMock(return_value=[])):
        engine_b.start_periodic_sync()
        for _ in range(100):
            if thread_started.is_set():
                break
            await asyncio.sleep(0.01)
        assert thread_started.is_set()
        await engine_b.stop()

    assert thread_finished.is_set()
    owner = await read_policy_owner()
    assert owner is not None
    assert owner.possible_partial is True

    # Ensure acquire_control_lock still works cleanly after cancellation.
    async with acquire_control_lock():
        assert settings.routing_enabled is True
