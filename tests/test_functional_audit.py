"""Acceptance regressions from the functional audit."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.cache import CachedResolution, SlidingWindowCache
from app.core.config import settings
from app.core.engine import ReconciliationEngine
from app.core.feeds_sync import refresh_feed
from app.core.resolver import NativeDNSResolver
from app.db.models import Feed, Target
from app.db.session import AsyncSessionLocal
from app.drivers.base import TargetCIDR
from app.drivers.mock import MockRouterDriver
from app.drivers.unifi import UniFiRouterDriver
from app.drivers.virtual_gateway import VirtualGatewayDriver
from gateway.wireguard_runtime import WireGuardRuntime


async def test_feed_refresh_preserves_unexpired_dns_retention(db_ready: None) -> None:
    now = datetime.now(UTC)
    resolution = CachedResolution(
        "edge.example.net",
        "198.51.100.7",
        "198.51.100.7/32",
        now,
        now,
        now + timedelta(hours=1),
        60,
    )
    resolver = NativeDNSResolver(SlidingWindowCache())
    resolver.resolve_fqdn = AsyncMock(return_value=[resolution])  # type: ignore[method-assign]
    driver = MockRouterDriver()
    engine = ReconciliationEngine(driver=driver, resolver=resolver)
    async with AsyncSessionLocal() as session:
        feed = Feed(name="Example", url="https://feed.example.net/list", format="plaintext")
        session.add(feed)
        await session.commit()
        with patch(
            "app.core.feeds_sync.download", AsyncMock(return_value=("edge.example.net", None))
        ):
            await refresh_feed(session, feed)
    assert (await engine.reconcile()).success
    assert await driver.get_current_routes() == {"198.51.100.7/32"}
    resolver.resolve_fqdn = AsyncMock(return_value=[])  # type: ignore[method-assign]
    # A DNS outage alone keeps retention; an unchanged HTTP 200 refresh must do so too.
    assert (await engine.reconcile()).success
    assert await driver.get_current_routes() == {"198.51.100.7/32"}
    async with AsyncSessionLocal() as session:
        stored = await session.get(Feed, feed.id)
        assert stored is not None
        with patch(
            "app.core.feeds_sync.download", AsyncMock(return_value=("edge.example.net", None))
        ):
            await refresh_feed(session, stored)
    assert (await engine.reconcile()).success
    assert await driver.get_current_routes() == {"198.51.100.7/32"}


async def test_ipv4_driver_rejects_unsupported_target_at_ingestion(
    client: httpx.AsyncClient,
) -> None:
    settings.router_driver = "mikrotik"
    # No router request: IPv6 is rejected inside apply_policy before HTTP is attempted.
    response = await client.post("/api/v1/targets", json={"value": "2001:db8::7/128"})
    assert response.status_code in {400, 422}
    assert (await client.get("/api/v1/targets")).json() == []


async def test_wireguard_reload_restores_previous_config_on_activation_failure(
    tmp_path: Path,
) -> None:
    valid = (
        "[Interface]\nPrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\n"
        "Address = 192.0.2.2/32\n[Peer]\n"
        "PublicKey = AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=\n"
        "AllowedIPs = 198.51.100.0/24\nEndpoint = edge.example.net:51820\n"
    )
    previous = valid.replace("192.0.2.2/32", "192.0.2.3/32")
    run = AsyncMock(return_value=(1, "", "activation failed"))
    runtime = WireGuardRuntime(run)
    runtime.source = tmp_path / "source.conf"
    runtime.active = tmp_path / "active.conf"
    runtime.source.write_text(valid)
    runtime.active.write_text(previous)
    with patch.object(runtime, "stop", AsyncMock()) as stop:
        with pytest.raises(RuntimeError, match="network command failed"):
            await runtime.reload()
        assert stop.await_count == 2
    assert runtime.active.read_text() == previous


async def test_unifi_reconciles_managed_client_scope() -> None:
    driver = UniFiRouterDriver()
    driver.controller_url = "https://router.example.net"
    driver.api_key = "test-placeholder"
    driver.vpn_network_id = "example-vpn"
    route: dict[str, object] = {
        "_id": "example-rule",
        "description": "TeByPass",
        "enabled": True,
        "network_id": "example-vpn",
        "ip_addresses": [],
        "target_devices": [{"type": "CLIENT", "client_mac": "02:00:00:00:00:01"}],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            route.update(json.loads(request.content))
        return httpx.Response(200, json=[route])

    with patch.object(
        driver, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle))
    ):
        result = await driver.apply_policy({TargetCIDR("198.51.100.7/32", "example")}, True)
    assert result.success
    assert route["target_devices"] == [{"type": "ALL_CLIENTS"}]


async def test_gateway_requires_fail_closed_policy_acknowledgement() -> None:
    driver = VirtualGatewayDriver()
    replies = [
        None,
        {"ok": True, "elements": []},
        {"ok": True, "elements": []},
        {"ok": True, "elements": []},
    ]
    with (
        patch.object(driver, "_socket_request", AsyncMock(side_effect=replies)),
        pytest.raises(RuntimeError, match="policy"),
    ):
        await driver.apply_policy(set(), False)


async def test_manual_and_feed_union_survives_pause_then_global_shutdown(db_ready: None) -> None:
    async with AsyncSessionLocal() as session:
        feed = Feed(
            name="Example",
            url="https://feed.example.net/list",
            blocked=True,
            last_fetched_at=datetime.now(UTC),
        )
        session.add(feed)
        await session.flush()
        session.add_all(
            [
                Target(value="198.51.100.7/32", destination_type="cidr", feed_id=feed.id),
                Target(value="198.51.100.7/32", destination_type="cidr"),
                Target(value="203.0.113.7/32", destination_type="cidr", feed_id=feed.id),
            ]
        )
        await session.commit()
    driver = MockRouterDriver()
    engine = ReconciliationEngine(driver=driver)
    assert (await engine.reconcile()).success
    assert await driver.get_current_routes() == {"198.51.100.7/32", "203.0.113.7/32"}
    async with AsyncSessionLocal() as session:
        stored = await session.get(Feed, feed.id)
        assert stored is not None
        stored.enabled = False
        await session.commit()
    assert (await engine.reconcile()).success
    assert await driver.get_current_routes() == {"198.51.100.7/32"}
    settings.routing_enabled = False
    assert (await engine.reconcile()).success
    assert not await driver.get_current_routes()


async def test_omada_rejects_ipv6_configuration_before_backend_failure(db_ready: None) -> None:
    from stopliga.errors import UnsupportedRouteShapeError
    from stopliga.routers.omada import _collapse_destinations

    from app.core.settings_store import save_settings

    with pytest.raises(UnsupportedRouteShapeError, match="IPv4"):
        _collapse_destinations(["2001:db8::7/128"])
    with pytest.raises(ValueError, match="IPv4"):
        await save_settings({"router_driver": "omada", "resolve_ipv6": True})


async def test_lifespan_bootstrap_persistence_restart_and_purge(
    client: httpx.AsyncClient,
) -> None:
    from app.main import app, lifespan

    settings.sync_interval_seconds = 0
    async with lifespan(app):
        feeds = (await client.get("/api/v1/feeds")).json()
        assert len(feeds) == 1 and feeds[0]["enabled"] is False
        response = await client.post("/api/v1/targets", json={"value": "203.0.113.7/32"})
        assert response.status_code == 201
        assert (await client.post("/api/v1/sync")).json()["success"]
        assert (await client.get("/api/v1/routes/active")).json() == ["203.0.113.7/32"]
    async with lifespan(app):
        assert len((await client.get("/api/v1/targets")).json()) == 1
        assert len((await client.get("/api/v1/feeds")).json()) == 1
        assert (await client.post("/api/v1/sync")).json()["success"]
        assert (await client.get("/api/v1/routes/active")).json() == ["203.0.113.7/32"]
        assert (await client.post("/api/v1/settings/router/purge")).json()["success"]
        assert (await client.get("/api/v1/routes/active")).json() == []
        assert not (await client.get("/api/v1/settings")).json()["routing_enabled"]
