"""Unit tests for the UniFi router driver and VPN networks API."""

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from app.core.config import Settings
from app.core.engine import ReconciliationEngine
from app.db.models import Target
from app.db.session import AsyncSessionLocal
from app.drivers.base import DriverHealth, TargetCIDR
from app.drivers.unifi import UniFiRouterDriver


@pytest.mark.asyncio
async def test_unifi_driver_unconfigured_fails() -> None:
    """Unconfigured production drivers must never simulate successful writes."""
    driver = UniFiRouterDriver()
    driver.controller_url = ""
    driver.api_key = None

    status = await driver.health_check()
    assert status.details.get("configured") is False

    sync_res = await driver.sync_routes(
        to_add={TargetCIDR(cidr="198.51.100.1/32", target_id="t1")},
        to_remove=set(),
    )
    assert sync_res.success is False
    assert sync_res.added_count == 0
    assert not driver._active_routes


@pytest.mark.asyncio
async def test_unifi_driver_mock_network_and_routes() -> None:
    """Driver should discover VPNs and perform PUT/POST on V2 traffic routes."""
    driver = UniFiRouterDriver()
    driver.controller_url = "https://198.51.100.1"
    driver.api_key = "test-api-key"
    driver.site = "default"
    driver.route_name = "TeByPass"

    fake_networks = [
        {
            "_id": "vpn-wg-1",
            "name": "WireGuard Client 1",
            "purpose": "vpn-client",
            "vpn_type": "wireguard-client",
        },
        {
            "_id": "net-lan-1",
            "name": "Default LAN",
            "purpose": "corporate",
        },
    ]

    fake_routes = [
        {
            "_id": "route-existing-1",
            "description": "TeByPass",
            "network_id": "vpn-wg-1",
            "enabled": True,
            "ip_addresses": [{"ip_or_subnet": "198.51.100.10/32", "ip_version": "v4"}],
        }
    ]

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "/api/self/sites" in url_str:
            return httpx.Response(200, json={"data": [{"name": "default"}]})
        if "/rest/networkconf" in url_str:
            return httpx.Response(200, json={"data": fake_networks})
        if "/v2/api/site/default/trafficroutes" in url_str:
            if request.method == "GET":
                return httpx.Response(200, json=fake_routes)
            if request.method == "PUT":
                fake_routes[0].update(json.loads(request.content))
                return httpx.Response(200, json={"success": True})
            if request.method == "POST":
                return httpx.Response(201, json={"success": True})
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(mock_handler)
    driver._client = lambda: httpx.AsyncClient(transport=transport)  # type: ignore[method-assign]

    # Test get_vpn_networks
    vpns = await driver.get_vpn_networks()
    assert len(vpns) == 1
    assert vpns[0]["id"] == "vpn-wg-1"
    assert vpns[0]["name"] == "WireGuard Client 1"

    # Test get_current_routes
    current_ips = await driver.get_current_routes()
    assert "198.51.100.10/32" in current_ips

    # Test sync_routes with PUT
    res = await driver.sync_routes(
        to_add={TargetCIDR(cidr="198.51.100.20/32", target_id="t2")},
        to_remove=set(),
    )
    assert res.success is True
    assert "198.51.100.20/32" in driver._active_routes
    assert "198.51.100.10/32" in driver._active_routes


@pytest.mark.asyncio
async def test_unifi_vpn_networks_api_endpoint(client: httpx.AsyncClient) -> None:
    """GET /api/v1/settings/unifi/vpn-networks returns list of VPNs."""
    mock_vpns = [{"id": "vpn-123", "name": "Mullvad-WG", "type": "wireguard-client"}]

    with patch.object(UniFiRouterDriver, "get_vpn_networks", return_value=mock_vpns):
        resp = await client.get("/api/v1/settings/unifi/vpn-networks")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "vpn-123"
        assert data[0]["name"] == "Mullvad-WG"


@pytest.mark.asyncio
async def test_unifi_teardown_and_purge_endpoint(client: httpx.AsyncClient) -> None:
    """UniFi teardown deletes the traffic route, and the purge endpoint sets routing_enabled=False."""
    deleted_urls: list[str] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if request.method == "GET" and "/trafficroutes" in url_str:
            return httpx.Response(
                200,
                json=[]
                if deleted_urls
                else [
                    {
                        "_id": "route-del-99",
                        "description": "TeByPass",
                        "matching_target": "IP",
                        "enabled": True,
                    }
                ],
            )
        if request.method == "DELETE" and "route-del-99" in url_str:
            deleted_urls.append(url_str)
            return httpx.Response(200, json={})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(mock_handler)
    driver = UniFiRouterDriver()
    driver.controller_url = "https://unifi.example.local"
    driver.api_key = "test-key"
    driver.route_name = "TeByPass"
    driver._client = lambda: httpx.AsyncClient(transport=transport)  # type: ignore[method-assign]

    await driver.teardown()
    assert len(deleted_urls) == 1
    assert "route-del-99" in deleted_urls[0]
    assert len(driver._active_routes) == 0

    # Test purge API endpoint
    with patch("app.core.settings_service.get_driver", return_value=driver):
        resp = await client.post("/api/v1/settings/router/purge")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True


@pytest.mark.asyncio
async def test_unifi_driver_avoids_unnecessary_network_polling() -> None:
    """Verify that get_status uses memory and get_current_routes caches without polling."""
    network_calls = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal network_calls
        network_calls += 1
        return httpx.Response(
            200,
            json=[
                {
                    "_id": "r-1",
                    "description": "TeByPass",
                    "ip_addresses": [{"ip_or_subnet": "203.0.113.50/32"}],
                }
            ],
        )

    transport = httpx.MockTransport(mock_handler)
    driver = UniFiRouterDriver()
    driver.controller_url = "https://unifi.example.local"
    driver.api_key = "test-key"
    driver._client = lambda: httpx.AsyncClient(transport=transport)  # type: ignore[method-assign]

    # 1. get_status should NOT make any network call
    status = driver.get_status()
    assert status.driver_name == "unifi"
    assert status.health.value == "degraded"
    assert network_calls == 0

    # 2. First get_current_routes loads from network (discover prefix + trafficroutes)
    routes = await driver.get_current_routes(force=False)
    assert routes == {"203.0.113.50/32"}
    assert network_calls == 2

    # 3. Subsequent get_current_routes uses memory (0 new network calls)
    routes2 = await driver.get_current_routes(force=False)
    assert routes2 == {"203.0.113.50/32"}
    assert network_calls == 2

    # 4. sync_routes with no changes does NOT make any network call
    res = await driver.sync_routes(to_add=set(), to_remove=set(), force=False)
    assert res.success is True
    assert res.added_count == 0
    assert network_calls == 2  # Still 2! No new calls!


async def test_disabled_policy_with_retained_members_stays_disabled() -> None:
    """A router can retain its member list while the rule is disabled."""
    driver = UniFiRouterDriver()
    driver.controller_url = "https://router.example.net"
    driver.api_key = "test-key"
    route = {
        "_id": "rule",
        "description": "TeByPass",
        "enabled": False,
        "network_id": "vpn",
        "ip_addresses": [{"ip_or_subnet": "198.51.100.7/32"}],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            route.update(json.loads(request.content))
            return httpx.Response(200, json=route)
        return httpx.Response(200, json=[route])

    driver._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle))  # type: ignore[method-assign]
    result = await driver.apply_policy(set(), False)
    assert result.success
    assert route["enabled"] is False
    assert route["ip_addresses"] == [
        {"ip_or_subnet": "198.51.100.7/32", "ip_version": "v4", "ports": [], "port_ranges": []}
    ]
    assert await driver.get_current_routes(force=True) == set()


async def test_unifi_apply_policy_skips_put_when_route_matches() -> None:
    """Matching remote route state skips PUT and post-write GET while updating observation."""
    driver = UniFiRouterDriver(
        Settings(
            router_driver="unifi",
            unifi_controller_url="https://router.example.net",
            unifi_api_key="test-key",
            unifi_vpn_network_id="vpn-1",
            unifi_kill_switch=False,
        )
    )
    route: dict[str, Any] = {
        "_id": "route-1",
        "description": "TeByPass",
        "matching_target": "IP",
        "enabled": True,
        "network_id": "vpn-1",
        "kill_switch_enabled": False,
        "target_devices": [{"type": "ALL_CLIENTS"}],
        "ip_addresses": [
            {"ip_or_subnet": "198.51.100.7/32", "ip_version": "v4", "ports": [], "port_ranges": []}
        ],
    }
    methods: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if "/api/self/sites" in str(request.url):
            return httpx.Response(200, json={"data": [{"name": "default"}]})
        methods.append(request.method)
        return httpx.Response(200, json=[route])

    driver._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle))  # type: ignore[method-assign]
    res = await driver.apply_policy({TargetCIDR("198.51.100.7/32", "t1")}, True)
    assert res.success is True
    assert res.added_count == 0
    assert res.removed_count == 0
    assert res.total_active == 1
    assert driver.policy_write_started is False
    assert methods == ["GET"]
    assert driver.get_status().health == DriverHealth.HEALTHY

    # Same behavior when the policy is already disabled on UniFi with retained members.
    route["enabled"] = False
    methods.clear()
    res_disabled = await driver.apply_policy(set(), False)
    assert res_disabled.success is True
    assert res_disabled.added_count == 0
    assert res_disabled.removed_count == 0
    assert res_disabled.total_active == 0
    assert driver.policy_write_started is False
    assert methods == ["GET"]


async def test_unifi_apply_policy_writes_on_drift() -> None:
    """Any difference in IPs, enabled flag, VPN, kill-switch, or target_devices triggers PUT."""
    driver = UniFiRouterDriver(
        Settings(
            router_driver="unifi",
            unifi_controller_url="https://router.example.net",
            unifi_api_key="test-key",
            unifi_vpn_network_id="vpn-1",
            unifi_kill_switch=False,
        )
    )
    route: dict[str, Any] = {
        "_id": "route-1",
        "description": "TeByPass",
        "matching_target": "IP",
        "enabled": True,
        "network_id": "vpn-1",
        "kill_switch_enabled": True,
        "target_devices": [{"type": "ALL_CLIENTS"}],
        "ip_addresses": [
            {"ip_or_subnet": "198.51.100.7/32", "ip_version": "v4", "ports": [], "port_ranges": []}
        ],
    }
    methods: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if "/api/self/sites" in str(request.url):
            return httpx.Response(200, json={"data": [{"name": "default"}]})
        methods.append(request.method)
        if request.method == "PUT":
            route.update(httpx.Response(200, content=request.content).json())
        return httpx.Response(200, json=[route])

    driver._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle))  # type: ignore[method-assign]
    res = await driver.apply_policy({TargetCIDR("198.51.100.7/32", "t1")}, True)
    assert res.success is True
    assert methods == ["GET", "PUT", "GET"]
    assert route["kill_switch_enabled"] is False

    # Next steady-state call makes only 1 GET and 0 PUTs.
    methods.clear()
    res_steady = await driver.apply_policy({TargetCIDR("198.51.100.7/32", "t1")}, True)
    assert res_steady.success is True
    assert methods == ["GET"]


async def test_unifi_reconcile_skips_redundant_health_check_and_put(db_ready: None) -> None:
    """ReconciliationEngine avoids redundant health_check() and PUT on steady-state UniFi cycles."""
    async with AsyncSessionLocal() as session:
        session.add(Target(value="198.51.100.10/32", destination_type="cidr"))
        await session.commit()

    driver = UniFiRouterDriver(
        Settings(
            router_driver="unifi",
            unifi_controller_url="https://router.example.net",
            unifi_api_key="test-key",
            unifi_vpn_network_id="vpn-1",
        )
    )
    route: dict[str, Any] = {
        "_id": "route-1",
        "description": "TeByPass",
        "matching_target": "IP",
        "enabled": True,
        "network_id": "vpn-1",
        "kill_switch_enabled": False,
        "target_devices": [{"type": "ALL_CLIENTS"}],
        "ip_addresses": [
            {"ip_or_subnet": "198.51.100.10/32", "ip_version": "v4", "ports": [], "port_ranges": []}
        ],
    }
    calls: list[tuple[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if "/api/self/sites" in str(request.url):
            return httpx.Response(200, json={"data": [{"name": "default"}]})
        return httpx.Response(200, json=[route])

    driver._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle))  # type: ignore[method-assign]
    engine = ReconciliationEngine(driver=driver)

    first = await engine.reconcile()
    assert first.success is True
    assert driver.get_status().health == DriverHealth.HEALTHY
    assert [m for m, p in calls if "trafficroutes" in p] == ["GET"]

    calls.clear()
    second = await engine.reconcile()
    assert second.success is True
    assert calls == [("GET", "/proxy/network/v2/api/site/default/trafficroutes")]
    assert driver.get_status().health == DriverHealth.HEALTHY
