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
