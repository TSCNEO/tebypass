"""Unit tests for the UniFi router driver and VPN networks API."""

from unittest.mock import patch

import httpx
import pytest

from app.drivers.base import TargetCIDR
from app.drivers.unifi import UniFiRouterDriver


@pytest.mark.asyncio
async def test_unifi_driver_unconfigured_fallback() -> None:
    """Unconfigured driver should behave as a local mock without raising."""
    driver = UniFiRouterDriver()
    driver.controller_url = ""
    driver.api_key = None

    status = await driver.health_check()
    assert status.details.get("configured") is False

    sync_res = await driver.sync_routes(
        to_add={TargetCIDR(cidr="198.51.100.1/32", target_id="t1")},
        to_remove=set(),
    )
    assert sync_res.success is True
    assert sync_res.added_count == 1
    assert "198.51.100.1/32" in driver._active_routes


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
                json=[
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
    with patch.object(UniFiRouterDriver, "teardown", return_value=None):
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
    assert status.health.value == "healthy"
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
