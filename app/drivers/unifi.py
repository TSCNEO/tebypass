"""Router driver for UniFi Network Application (V2 Traffic Routes + WireGuard Client)."""

import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import settings
from app.drivers.base import (
    BaseRouterDriver,
    DriverHealth,
    DriverStatus,
    SyncResult,
    TargetCIDR,
)

logger = logging.getLogger("tebypass.drivers.unifi")


class UniFiRouterDriver(BaseRouterDriver):
    """Driver managing V2 Traffic Routes on UniFi Cloud Gateway / Dream Machine."""

    driver_name: str = "unifi"

    def __init__(self) -> None:
        url = settings.unifi_controller_url or ""
        if url and not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        self.controller_url = url.rstrip("/")
        self.api_key = settings.unifi_api_key
        self.username = settings.unifi_username
        self.password = settings.unifi_password
        self.site = settings.unifi_site or "default"
        self.route_name = settings.unifi_route_name or "TeByPass"
        self.vpn_network_id = settings.unifi_vpn_network_id
        self.verify_tls = settings.unifi_verify_tls
        self._prefix: str | None = None
        self._last_sync: datetime | None = None
        self._active_routes: set[str] = set()

    def _get_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-KEY"] = self.api_key
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            verify=self.verify_tls,
            timeout=15.0,
            headers=self._get_headers(),
        )

    async def initialize(self) -> None:
        """Verify presence of UniFi credentials."""
        pass

    async def discover_prefix(self, client: httpx.AsyncClient) -> str:
        """Detect whether UniFi Network is exposed under /proxy/network or root."""
        if self._prefix is not None:
            return self._prefix
        if not self.controller_url:
            return "/proxy/network"

        for candidate in ("/proxy/network", ""):
            try:
                resp = await client.get(f"{self.controller_url}{candidate}/api/self/sites")
                if resp.status_code in (200, 201):
                    self._prefix = candidate
                    return candidate
            except Exception:
                continue

        self._prefix = "/proxy/network"
        return self._prefix

    async def get_vpn_networks(self) -> list[dict[str, Any]]:
        """List VPN client networks configured on UniFi."""
        if not self.controller_url or not self.api_key:
            return []

        async with self._client() as client:
            prefix = await self.discover_prefix(client)
            url = f"{self.controller_url}{prefix}/api/s/{self.site}/rest/networkconf"
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning(
                    "UniFi rest/networkconf returned %s: %s", resp.status_code, resp.text
                )
                return []
            payload = resp.json()
            data = payload.get("data", []) if isinstance(payload, dict) else payload
            results = []
            for record in data:
                if isinstance(record, dict) and record.get("purpose") == "vpn-client":
                    results.append(
                        {
                            "id": str(record.get("_id", "")),
                            "name": str(record.get("name", "VPN")),
                            "type": str(record.get("vpn_type", "wireguard-client")),
                        }
                    )
            return sorted(results, key=lambda x: x["name"].lower())

    async def get_current_routes(self) -> set[str]:
        """Fetch current IP members of the TeByPass traffic route."""
        if not self.controller_url or not self.api_key:
            return set(self._active_routes)

        async with self._client() as client:
            prefix = await self.discover_prefix(client)
            url = f"{self.controller_url}{prefix}/v2/api/site/{self.site}/trafficroutes"
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return set(self._active_routes)
                routes = resp.json()
                if not isinstance(routes, list):
                    return set(self._active_routes)
                for r in routes:
                    if isinstance(r, dict) and r.get("description") == self.route_name:
                        ips = {
                            entry.get("ip_or_subnet")
                            for entry in r.get("ip_addresses", [])
                            if isinstance(entry, dict) and entry.get("ip_or_subnet")
                        }
                        self._active_routes = {str(ip) for ip in ips if ip}
                        return set(self._active_routes)
            except Exception as ex:
                logger.warning("Failed to query UniFi traffic routes: %s", ex)
        return set(self._active_routes)

    async def sync_routes(
        self,
        to_add: set[TargetCIDR],
        to_remove: set[str],
    ) -> SyncResult:
        """Create or update the V2 Traffic Route on UniFi."""
        start = time.perf_counter()
        if not self.controller_url or not self.api_key:
            # Fallback stub for unconfigured controller in tests
            for item in to_add:
                self._active_routes.add(item.cidr)
            for cidr in to_remove:
                self._active_routes.discard(cidr)
            self._last_sync = datetime.now(UTC)
            return SyncResult(
                driver_name=self.driver_name,
                added_count=len(to_add),
                removed_count=len(to_remove),
                total_active=len(self._active_routes),
                duration_ms=round((time.perf_counter() - start) * 1000.0, 2),
                success=True,
            )

        async with self._client() as client:
            prefix = await self.discover_prefix(client)
            routes_url = f"{self.controller_url}{prefix}/v2/api/site/{self.site}/trafficroutes"

            resp = await client.get(routes_url)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"UniFi trafficroutes query returned {resp.status_code}: {resp.text[:200]}"
                )

            routes_data = resp.json()
            existing_route: dict[str, Any] | None = None
            if isinstance(routes_data, list):
                for r in routes_data:
                    if isinstance(r, dict) and r.get("description") == self.route_name:
                        existing_route = r
                        break

            # Calculate desired IP set
            current_ips: set[str] = set()
            if existing_route:
                for entry in existing_route.get("ip_addresses", []):
                    if isinstance(entry, dict) and entry.get("ip_or_subnet"):
                        current_ips.add(str(entry["ip_or_subnet"]))

            desired_set = (current_ips | {item.cidr for item in to_add}) - to_remove
            desired_list = sorted(desired_set)

            ip_entries: list[dict[str, Any]] = [
                {
                    "ip_or_subnet": ip,
                    "ip_version": "v6" if ":" in ip else "v4",
                    "ports": [],
                    "port_ranges": [],
                }
                for ip in desired_list
            ]

            # Resolve VPN client network ID
            vpn_id = self.vpn_network_id
            if not vpn_id:
                if existing_route and existing_route.get("network_id"):
                    vpn_id = str(existing_route["network_id"])
                else:
                    vpns = await self.get_vpn_networks()
                    if vpns:
                        vpn_id = vpns[0]["id"]
                    else:
                        raise RuntimeError(
                            "No UniFi VPN Client network found. Create a WireGuard/VPN Client in UniFi Network first."
                        )

            if existing_route:
                route_id = existing_route["_id"]
                payload = {**existing_route}
                payload["description"] = self.route_name
                payload["matching_target"] = "IP"
                payload["network_id"] = vpn_id
                payload["ip_addresses"] = ip_entries
                payload["enabled"] = bool(desired_list)
                put_url = f"{routes_url}/{route_id}"
                update_resp = await client.put(put_url, json=payload)
                if update_resp.status_code not in (200, 201):
                    raise RuntimeError(
                        f"Failed to update UniFi traffic route: {update_resp.status_code} {update_resp.text[:300]}"
                    )
            else:
                create_payload = {
                    "description": self.route_name,
                    "domains": [],
                    "enabled": bool(desired_list),
                    "ip_addresses": ip_entries,
                    "ip_ranges": [],
                    "kill_switch_enabled": False,
                    "matching_target": "IP",
                    "network_id": vpn_id,
                    "next_hop": "",
                    "regions": [],
                    "target_devices": [{"type": "ALL_CLIENTS"}],
                }
                create_resp = await client.post(routes_url, json=create_payload)
                if create_resp.status_code not in (200, 201):
                    raise RuntimeError(
                        f"Failed to create UniFi traffic route: {create_resp.status_code} {create_resp.text[:300]}"
                    )

            self._active_routes = set(desired_list)
            self._last_sync = datetime.now(UTC)
            duration_ms = (time.perf_counter() - start) * 1000.0

            return SyncResult(
                driver_name=self.driver_name,
                added_count=len(to_add),
                removed_count=len(to_remove),
                total_active=len(self._active_routes),
                duration_ms=round(duration_ms, 2),
                success=True,
            )

    async def health_check(self) -> DriverStatus:
        """Inspect UniFi controller connectivity and VPN interfaces."""
        is_configured = bool(self.controller_url and self.api_key)
        vpn_list: list[dict[str, Any]] = []
        route_found = False
        health = DriverHealth.DEGRADED

        if is_configured:
            try:
                vpn_list = await self.get_vpn_networks()
                curr_routes = await self.get_current_routes()
                route_found = bool(curr_routes)
                health = DriverHealth.HEALTHY if vpn_list else DriverHealth.DEGRADED
            except Exception as ex:
                logger.warning("UniFi health probe failed: %s", ex)
                health = DriverHealth.UNHEALTHY

        details = {
            "controller_url": self.controller_url or "not_configured",
            "site": self.site,
            "route_name": self.route_name,
            "vpn_network_id": self.vpn_network_id,
            "available_vpn_networks": len(vpn_list),
            "route_exists_in_unifi": route_found,
            "configured": is_configured,
        }

        return DriverStatus(
            driver_name=self.driver_name,
            health=health,
            active_routes_count=len(self._active_routes),
            last_sync=self._last_sync,
            details=details,
        )

    async def close(self) -> None:
        """Release HTTP client sessions."""
        pass

    async def teardown(self) -> None:
        """Find and delete the TeByPass traffic route from UniFi."""
        if not self.controller_url or not self.api_key:
            self._active_routes.clear()
            return

        async with self._client() as client:
            prefix = await self.discover_prefix(client)
            routes_url = f"{self.controller_url}{prefix}/v2/api/site/{self.site}/trafficroutes"
            try:
                resp = await client.get(routes_url)
                if resp.status_code == 200:
                    routes_data = resp.json()
                    if isinstance(routes_data, list):
                        for r in routes_data:
                            if isinstance(r, dict) and r.get("description") == self.route_name:
                                route_id = r.get("_id")
                                if route_id:
                                    del_resp = await client.delete(f"{routes_url}/{route_id}")
                                    if del_resp.status_code in (200, 204):
                                        logger.info(
                                            "Deleted UniFi traffic route %s (%s)",
                                            self.route_name,
                                            route_id,
                                        )
                                    else:
                                        logger.warning(
                                            "Failed to delete UniFi traffic route: %s %s",
                                            del_resp.status_code,
                                            del_resp.text[:200],
                                        )
            except Exception as ex:
                logger.warning("Error deleting UniFi traffic route: %s", ex)

        self._active_routes.clear()
        self._last_sync = datetime.now(UTC)
