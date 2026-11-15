"""C02 regressions: atomic ResolvedIP upserts, concurrent races, migration, and retention."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.cache import CachedResolution, SlidingWindowCache
from app.core.config import settings
from app.core.engine import ReconciliationEngine
from app.core.resolver import NativeDNSResolver
from app.db.models import ResolvedIP, Target
from app.db.session import AsyncSessionLocal, _apply_lightweight_migrations
from app.drivers.mock import MockRouterDriver


def _cached(fqdn: str, ip: str, expires_at: datetime, ttl: int = 60) -> CachedResolution:
    now = datetime.now(UTC)
    return CachedResolution(
        fqdn=fqdn,
        ip_address=ip,
        cidr=f"{ip}/32",
        first_seen_at=now,
        last_seen_at=now,
        expires_at=expires_at,
        dns_ttl=ttl,
    )


async def test_concurrent_resolve_api_and_engine_reconcile_create_single_row(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Racing API resolve and engine reconcile for the same FQDN must not duplicate rows."""
    monkeypatch.setattr("app.core.routes_service._trigger_background_reconciliation", AsyncMock())
    created = await client.post("/api/v1/targets", json={"value": "race.example.net"})
    assert created.status_code == 201
    target_id = created.json()["id"]

    barrier = asyncio.Barrier(2)
    expires = datetime.now(UTC) + timedelta(hours=1)

    async def barrier_resolve(
        self: Any, fqdn: str, window_extension_seconds: int | None = None
    ) -> list[CachedResolution]:
        await barrier.wait()
        return [_cached(fqdn, "198.51.100.10", expires)]

    monkeypatch.setattr("app.core.resolver.NativeDNSResolver.resolve_fqdn", barrier_resolve)
    driver = MockRouterDriver()
    engine = ReconciliationEngine(driver=driver)

    api_resp, sync_res = await asyncio.gather(
        client.post(f"/api/v1/targets/{target_id}/resolve"),
        engine.reconcile(),
    )

    assert api_resp.status_code == 200
    assert api_resp.json()["ips"] == ["198.51.100.10"]
    assert sync_res.success

    async with AsyncSessionLocal() as session:
        rows = (
            (await session.execute(select(ResolvedIP).where(ResolvedIP.target_id == target_id)))
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].ip_address == "198.51.100.10"

    # Follow-up cycles must keep succeeding without MultipleResultsFound.
    async def steady_resolve(
        self: Any, fqdn: str, window_extension_seconds: int | None = None
    ) -> list[CachedResolution]:
        return [_cached(fqdn, "198.51.100.10", expires + timedelta(minutes=5))]

    monkeypatch.setattr("app.core.resolver.NativeDNSResolver.resolve_fqdn", steady_resolve)
    assert (await engine.reconcile()).success
    assert (await engine.reconcile()).success


async def test_concurrent_create_and_bulk_import_race_with_reconcile(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent create, bulk import, and reconcile converge cleanly with unique rows."""
    monkeypatch.setattr("app.core.routes_service._trigger_background_reconciliation", AsyncMock())
    monkeypatch.setattr("app.core.config.settings.sliding_window_seconds", 3600)

    expires = datetime.now(UTC) + timedelta(hours=1)

    async def multi_ip_resolve(
        self: Any, fqdn: str, window_extension_seconds: int | None = None
    ) -> list[CachedResolution]:
        await asyncio.sleep(0.01)
        return [
            _cached(fqdn, "198.51.100.21", expires),
            _cached(fqdn, "198.51.100.22", expires),
        ]

    monkeypatch.setattr("app.core.resolver.NativeDNSResolver.resolve_fqdn", multi_ip_resolve)
    engine = ReconciliationEngine(driver=MockRouterDriver())

    create_task = client.post("/api/v1/targets", json={"value": "alpha.example.net"})
    import_task = client.post(
        "/api/v1/targets/bulk",
        json={"entries": ["beta.example.net\n198.51.100.99/32"]},
    )
    reconcile_task = engine.reconcile()

    create_resp, import_resp, sync_res = await asyncio.gather(
        create_task, import_task, reconcile_task
    )
    assert create_resp.status_code == 201
    assert import_resp.status_code == 200
    assert import_resp.json()["created"] == 2
    assert sync_res.success

    # Follow-up reconcile sees all targets cleanly without duplicates.
    final_sync = await engine.reconcile()
    assert final_sync.success
    assert await engine.driver.get_current_routes() == {
        "198.51.100.21/32",
        "198.51.100.22/32",
        "198.51.100.99/32",
    }


async def test_multiple_ips_never_shorten_retention_and_survive_restart(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shorter later expirations never truncate longer retention, and all IPs survive restart."""
    monkeypatch.setattr("app.core.routes_service._trigger_background_reconciliation", AsyncMock())
    now = datetime.now(UTC)
    long_expiry_a = now + timedelta(hours=3)
    long_expiry_b = now + timedelta(hours=4)
    short_expiry_a = now + timedelta(minutes=10)

    async def initial_resolve(
        self: Any, fqdn: str, window_extension_seconds: int | None = None
    ) -> list[CachedResolution]:
        return [
            _cached(fqdn, "198.51.100.31", long_expiry_a, ttl=300),
            _cached(fqdn, "198.51.100.32", long_expiry_b, ttl=300),
        ]

    monkeypatch.setattr("app.core.resolver.NativeDNSResolver.resolve_fqdn", initial_resolve)
    created = await client.post("/api/v1/targets", json={"value": "cdn.example.net"})
    assert created.status_code == 201
    target_id = created.json()["id"]

    # Later API resolve with a shorter expiration must not shorten stored retention.
    async def shorter_resolve(
        self: Any, fqdn: str, window_extension_seconds: int | None = None
    ) -> list[CachedResolution]:
        return [_cached(fqdn, "198.51.100.31", short_expiry_a, ttl=30)]

    monkeypatch.setattr("app.core.resolver.NativeDNSResolver.resolve_fqdn", shorter_resolve)
    resolved = await client.post(f"/api/v1/targets/{target_id}/resolve")
    assert resolved.status_code == 200

    async with AsyncSessionLocal() as session:
        rows = (
            (await session.execute(select(ResolvedIP).where(ResolvedIP.target_id == target_id)))
            .scalars()
            .all()
        )
    by_ip = {row.ip_address: row for row in rows}
    assert set(by_ip) == {"198.51.100.31", "198.51.100.32"}
    assert by_ip["198.51.100.31"].expires_at.replace(tzinfo=UTC) >= long_expiry_a - timedelta(
        seconds=1
    )
    assert by_ip["198.51.100.32"].expires_at.replace(tzinfo=UTC) >= long_expiry_b - timedelta(
        seconds=1
    )

    # Simulate process restart with fresh engine/cache and transient DNS outage: retention holds.
    monkeypatch.setattr(
        "app.core.resolver.NativeDNSResolver.resolve_fqdn",
        AsyncMock(return_value=[]),
    )
    fresh_driver = MockRouterDriver()
    fresh_engine = ReconciliationEngine(
        driver=fresh_driver,
        cache=SlidingWindowCache(),
        resolver=NativeDNSResolver(SlidingWindowCache()),
    )
    assert (await fresh_engine.reconcile()).success
    assert await fresh_driver.get_current_routes() == {"198.51.100.31/32", "198.51.100.32/32"}


async def test_migration_deduplicates_legacy_resolved_ips_and_preserves_longest_retention(
    tmp_path: Path,
) -> None:
    """An existing DB already corrupted by duplicate ResolvedIP rows migrates cleanly."""
    legacy_db = tmp_path / "legacy.db"
    eng = create_async_engine(f"sqlite+aiosqlite:///{legacy_db}")
    target_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    t_early = (now - timedelta(hours=2)).replace(tzinfo=None).isoformat(sep=" ")
    t_mid = (now - timedelta(minutes=10)).replace(tzinfo=None).isoformat(sep=" ")
    exp_short = (now + timedelta(minutes=15)).replace(tzinfo=None).isoformat(sep=" ")
    exp_long = (now + timedelta(hours=5)).replace(tzinfo=None).isoformat(sep=" ")

    try:
        async with eng.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE targets ("
                    "id VARCHAR(36) PRIMARY KEY, "
                    "feed_id VARCHAR(36), "
                    "destination_type VARCHAR(16) NOT NULL, "
                    "value VARCHAR(255) NOT NULL, "
                    "description VARCHAR(255), "
                    "enabled BOOLEAN, "
                    "created_at DATETIME)"
                )
            )
            await conn.execute(
                text("CREATE TABLE feeds (id VARCHAR(36) PRIMARY KEY, name VARCHAR(128))")
            )
            await conn.execute(
                text(
                    "CREATE TABLE resolved_ips ("
                    "id VARCHAR(36) PRIMARY KEY, "
                    "target_id VARCHAR(36) NOT NULL, "
                    "ip_address VARCHAR(64) NOT NULL, "
                    "cidr VARCHAR(68) NOT NULL, "
                    "first_seen_at DATETIME, "
                    "last_seen_at DATETIME, "
                    "expires_at DATETIME NOT NULL, "
                    "ttl_seconds INTEGER)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO targets (id, destination_type, value, enabled, created_at) "
                    "VALUES (:id, 'fqdn', 'legacy.example.net', 1, :created)"
                ),
                {"id": target_id, "created": t_early},
            )
            await conn.execute(
                text(
                    "INSERT INTO resolved_ips "
                    "(id, target_id, ip_address, cidr, first_seen_at, last_seen_at, expires_at, ttl_seconds) "
                    "VALUES (:id1, :tid, '198.51.100.77', '198.51.100.77/32', :f1, :l1, :e1, 60), "
                    "(:id2, :tid, '198.51.100.77', '198.51.100.77/32', :f2, :l2, :e2, 300)"
                ),
                {
                    "id1": str(uuid.uuid4()),
                    "id2": str(uuid.uuid4()),
                    "tid": target_id,
                    "f1": t_early,
                    "l1": t_early,
                    "e1": exp_short,
                    "f2": t_mid,
                    "l2": t_mid,
                    "e2": exp_long,
                },
            )

        async with eng.begin() as conn:
            await _apply_lightweight_migrations(conn)

        async with eng.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT first_seen_at, last_seen_at, expires_at, ttl_seconds "
                        "FROM resolved_ips WHERE target_id = :tid"
                    ),
                    {"tid": target_id},
                )
            ).fetchall()
            assert len(rows) == 1
            assert rows[0][0].startswith(t_early[:19])
            assert rows[0][1].startswith(t_mid[:19])
            assert rows[0][2].startswith(exp_long[:19])
            assert rows[0][3] == 300

        with pytest.raises(IntegrityError):
            async with eng.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO resolved_ips "
                        "(id, target_id, ip_address, cidr, first_seen_at, last_seen_at, expires_at, ttl_seconds) "
                        "VALUES (:id, :tid, '198.51.100.77', '198.51.100.77/32', :t, :t, :t, 60)"
                    ),
                    {"id": str(uuid.uuid4()), "tid": target_id, "t": exp_long},
                )
    finally:
        await eng.dispose()


async def test_concurrent_target_deletion_does_not_break_unrelated_targets(
    db_ready: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleting one target mid-reconcile skips only that target and applies unrelated ones."""
    monkeypatch.setattr(settings, "sliding_window_seconds", 3600)
    async with AsyncSessionLocal() as session:
        vanishing = Target(value="vanishing.example.net", destination_type="fqdn")
        steady = Target(value="steady.example.net", destination_type="fqdn")
        session.add_all([vanishing, steady])
        await session.commit()
        vanishing_id = vanishing.id

    expires = datetime.now(UTC) + timedelta(hours=1)

    async def resolve_with_delete(
        self: Any, fqdn: str, window_extension_seconds: int | None = None
    ) -> list[CachedResolution]:
        if fqdn == "vanishing.example.net":
            async with AsyncSessionLocal() as deleter:
                row = await deleter.get(Target, vanishing_id)
                if row is not None:
                    await deleter.delete(row)
                    await deleter.commit()
            return [_cached(fqdn, "198.51.100.200", expires)]
        return [_cached(fqdn, "198.51.100.201", expires)]

    monkeypatch.setattr("app.core.resolver.NativeDNSResolver.resolve_fqdn", resolve_with_delete)
    driver = MockRouterDriver()
    engine = ReconciliationEngine(driver=driver)
    res = await engine.reconcile()

    assert res.success
    assert await driver.get_current_routes() == {"198.51.100.201/32"}
    async with AsyncSessionLocal() as session:
        orphan = (
            await session.execute(select(ResolvedIP).where(ResolvedIP.target_id == vanishing_id))
        ).scalar_one_or_none()
        assert orphan is None
