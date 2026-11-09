"""Retention must affect routing across failed resolution and target deletion."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from app.core.cache import SlidingWindowCache
from app.core.engine import ReconciliationEngine
from app.core.resolver import NativeDNSResolver
from app.db.models import ResolvedIP, Target
from app.db.session import AsyncSessionLocal
from app.drivers.mock import MockRouterDriver


async def test_persisted_retention_survives_dns_failure_but_not_disabled_target(
    db_ready: None,
) -> None:
    async with AsyncSessionLocal() as session:
        target = Target(value="edge.example.net", destination_type="fqdn")
        session.add(target)
        await session.flush()
        target_id = target.id
        session.add(
            ResolvedIP(
                target_id=target_id,
                ip_address="198.51.100.7",
                cidr="198.51.100.7/32",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await session.commit()
    resolver = NativeDNSResolver(SlidingWindowCache())
    resolver.resolve_fqdn = AsyncMock(return_value=[])  # type: ignore[method-assign]
    driver = MockRouterDriver()
    engine = ReconciliationEngine(driver=driver, resolver=resolver)
    assert (await engine.reconcile()).success
    assert await driver.get_current_routes() == {"198.51.100.7/32"}
    async with AsyncSessionLocal() as session:
        stored = await session.get(Target, target_id)
        assert stored is not None
        stored.enabled = False
        await session.commit()
    assert (await engine.reconcile()).success
    assert not await driver.get_current_routes()
