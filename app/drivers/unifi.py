"""Router driver for UniFi Network Application (V2 Traffic Routes + WireGuard Client)."""

import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import Settings, settings
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
    retains_policy_when_disabled: bool = True
    health_observed_during_apply: bool = True

    def __init__(self, cfg: Settings | None = None) -> None:
        cfg = cfg or settings
        url = cfg.unifi_controller_url or ""
        if url and not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        self.controller_url = url.rstrip("/")
        self.api_key = cfg.unifi_api_key
        self.username = cfg.unifi_username
        self.password = cfg.unifi_password
        self.site = cfg.unifi_site or "default"
        self.route_name = cfg.unifi_route_name or "TeByPass"
        self.vpn_network_id = cfg.unifi_vpn_network_id
        self.verify_tls = cfg.unifi_verify_tls
        self.kill_switch = cfg.unifi_kill_switch
        self._prefix: str | None = None
        self._last_sync: datetime | None = None
        self._observed_at: datetime | None = None
        self._active_routes: set[str] = set()
        self._observed_kill_switch: bool | None = None
        self._routes_loaded: bool = False
        self._last_health_check: datetime | None = None
        self._cached_health_status: DriverStatus | None = None

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
            raise RuntimeError("UniFi controller URL and API key are required")

        async with self._client() as client:
            prefix = await self.discover_prefix(client)
            url = f"{self.controller_url}{prefix}/api/s/{self.site}/rest/networkconf"
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning(
                    "UniFi rest/networkconf returned %s: %s", resp.status_code, resp.text[:200]
                )
                raise RuntimeError(
                    f"UniFi controller returned HTTP {resp.status_code} while listing VPN networks"
                )
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

    async def get_current_routes(self, force: bool = False) -> set[str]:
        """Fetch current IP members of the TeByPass traffic route."""
        if not self.controller_url or not self.api_key:
            raise RuntimeError("UniFi controller URL and API key are required")

        if self._routes_loaded and not force:
            return set(self._active_routes)

        async with self._client() as client:
            prefix = await self.discover_prefix(client)
            url = f"{self.controller_url}{prefix}/v2/api/site/{self.site}/trafficroutes"
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                routes = resp.json()
                self._observed_at = datetime.now(UTC)
                if not isinstance(routes, list):
                    raise RuntimeError("Invalid UniFi routes response")
                for r in routes:
                    if isinstance(r, dict) and r.get("description") == self.route_name:
                        ips = {
                            entry.get("ip_or_subnet")
                            for entry in r.get("ip_addresses", [])
                            if isinstance(entry, dict) and entry.get("ip_or_subnet")
                        }
                        self._active_routes = (
                            {str(ip) for ip in ips if ip} if r.get("enabled", True) else set()
                        )
                        self._observed_kill_switch = (
                            bool(r.get("kill_switch_enabled"))
                            if "kill_switch_enabled" in r
                            else None
                        )
                        self._routes_loaded = True
                        return set(self._active_routes)
                self._active_routes.clear()
                self._observed_kill_switch = None
                self._routes_loaded = True
            except Exception as ex:
                raise RuntimeError("Failed to query UniFi traffic routes") from ex
        return set(self._active_routes)

    def _route_matches_desired(
        self,
        existing_route: dict[str, Any],
        *,
        desired_list: list[str],
        requested_enabled: bool,
        vpn_id: str,
    ) -> bool:
        """Return True when the remote UniFi Traffic Route already matches the full policy."""
        if existing_route.get("matching_target") != "IP":
            return False
        if existing_route.get("network_id") != vpn_id:
            return False
        enabled_val = existing_route.get("enabled")
        if not isinstance(enabled_val, bool) or enabled_val != requested_enabled:
            return False
        if existing_route.get("target_devices") != [{"type": "ALL_CLIENTS"}]:
            return False
        if "kill_switch_enabled" in existing_route:
            if bool(existing_route.get("kill_switch_enabled")) != self.kill_switch:
                return False
        elif self.kill_switch:
            return False
        raw_entries = existing_route.get("ip_addresses")
        if not isinstance(raw_entries, list) or len(raw_entries) != len(desired_list):
            return False
        existing_ips = {
            str(entry["ip_or_subnet"])
            for entry in raw_entries
            if isinstance(entry, dict) and entry.get("ip_or_subnet")
        }
        return existing_ips == set(desired_list)

    async def apply_policy(self, targets: set[TargetCIDR], enabled: bool) -> SyncResult:
        if not self.controller_url or not self.api_key:
            raise RuntimeError("UniFi controller URL and API key are required")
        desired = {target.cidr for target in targets} if enabled else set()
        result = await self.sync_routes(
            set(),
            set(),
            force=True,
            desired_cidrs=desired,
            policy_enabled=enabled,
        )
        if result.success and self._active_routes != desired:
            raise RuntimeError("UniFi did not retain the requested policy")
        return result

    async def sync_routes(
        self,
        to_add: set[TargetCIDR],
        to_remove: set[str],
        force: bool = False,
        desired_cidrs: set[str] | None = None,
        policy_enabled: bool | None = None,
    ) -> SyncResult:
        """Create or update the V2 Traffic Route on UniFi."""
        start = time.perf_counter()
        if not self.controller_url or not self.api_key:
            return SyncResult(
                driver_name=self.driver_name,
                added_count=0,
                removed_count=0,
                total_active=len(self._active_routes),
                duration_ms=0,
                success=False,
                error_message="UniFi controller URL and API key are required",
            )

        if not to_add and not to_remove and not force and self._routes_loaded:
            logger.debug("No route changes needed on UniFi; skipping remote call.")
            return SyncResult(
                driver_name=self.driver_name,
                added_count=0,
                removed_count=0,
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
            if not isinstance(routes_data, list):
                raise RuntimeError("Invalid UniFi routes response")
            self._observed_at = datetime.now(UTC)
            existing_route: dict[str, Any] | None = None
            if (
                sum(
                    isinstance(route, dict) and route.get("description") == self.route_name
                    for route in routes_data
                )
                > 1
            ):
                raise RuntimeError("Duplicate managed UniFi routes")
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

            desired_set = (
                desired_cidrs
                if desired_cidrs is not None
                else (current_ips | {item.cidr for item in to_add}) - to_remove
            )
            requested_enabled = (
                bool(desired_set)
                if policy_enabled is None
                else bool(policy_enabled and desired_set)
            )
            # UniFi rejects an empty IP match even when disabling a policy. Keep its last
            # members as configuration while reporting no effective active routes.
            desired_list = sorted(desired_set if requested_enabled else current_ips)
            effective_current = (
                current_ips if (existing_route and existing_route.get("enabled", True)) else set()
            )
            effective_desired = set(desired_list) if requested_enabled else set()

            if not existing_route and not desired_list:
                now = datetime.now(UTC)
                self._active_routes.clear()
                self._observed_kill_switch = None
                self._routes_loaded = True
                self._observed_at = now
                self._last_sync = now
                return SyncResult(
                    driver_name=self.driver_name,
                    added_count=0,
                    removed_count=0,
                    total_active=0,
                    duration_ms=round((time.perf_counter() - start) * 1000.0, 2),
                    success=True,
                )

            # Resolve VPN client network ID
            vpn_id = self.vpn_network_id
            if not vpn_id:
                if existing_route and existing_route.get("network_id"):
                    vpn_id = str(existing_route["network_id"])
                else:
                    vpns = await self.get_vpn_networks()
                    if len(vpns) == 1:
                        vpn_id = vpns[0]["id"]
                    elif len(vpns) > 1:
                        raise RuntimeError(
                            "Multiple UniFi VPN Client networks found; select unifi_vpn_network_id explicitly"
                        )
                    else:
                        raise RuntimeError(
                            "No UniFi VPN Client network found. Create a WireGuard/VPN Client in UniFi Network first."
                        )

            if existing_route is not None and self._route_matches_desired(
                existing_route,
                desired_list=desired_list,
                requested_enabled=requested_enabled,
                vpn_id=vpn_id,
            ):
                logger.debug("UniFi traffic route already matches desired policy; skipping PUT.")
                now = datetime.now(UTC)
                self._active_routes = effective_desired
                self._observed_kill_switch = (
                    bool(existing_route.get("kill_switch_enabled"))
                    if "kill_switch_enabled" in existing_route
                    else self.kill_switch
                )
                self._routes_loaded = True
                self._observed_at = now
                self._last_sync = now
                return SyncResult(
                    driver_name=self.driver_name,
                    added_count=0,
                    removed_count=0,
                    total_active=len(self._active_routes),
                    duration_ms=round((time.perf_counter() - start) * 1000.0, 2),
                    success=True,
                )

            ip_entries: list[dict[str, Any]] = [
                {
                    "ip_or_subnet": ip,
                    "ip_version": "v6" if ":" in ip else "v4",
                    "ports": [],
                    "port_ranges": [],
                }
                for ip in desired_list
            ]

            if existing_route:
                route_id = existing_route["_id"]
                payload = {**existing_route}
                payload["description"] = self.route_name
                payload["matching_target"] = "IP"
                payload["network_id"] = vpn_id
                payload["ip_addresses"] = ip_entries
                payload["enabled"] = requested_enabled
                payload["kill_switch_enabled"] = self.kill_switch
                payload["target_devices"] = [{"type": "ALL_CLIENTS"}]
                put_url = f"{routes_url}/{route_id}"
                self.policy_write_started = True
                update_resp = await client.put(put_url, json=payload)
                if update_resp.status_code not in (200, 201):
                    raise RuntimeError(
                        f"Failed to update UniFi traffic route: {update_resp.status_code} {update_resp.text[:300]}"
                    )
            else:
                create_payload = {
                    "description": self.route_name,
                    "domains": [],
                    "enabled": requested_enabled,
                    "ip_addresses": ip_entries,
                    "ip_ranges": [],
                    "kill_switch_enabled": self.kill_switch,
                    "matching_target": "IP",
                    "network_id": vpn_id,
                    "next_hop": "",
                    "regions": [],
                    "target_devices": [{"type": "ALL_CLIENTS"}],
                }
                self.policy_write_started = True
                create_resp = await client.post(routes_url, json=create_payload)
                if create_resp.status_code not in (200, 201):
                    raise RuntimeError(
                        f"Failed to create UniFi traffic route: {create_resp.status_code} {create_resp.text[:300]}"
                    )

            verified_response = await client.get(routes_url)
            verified_response.raise_for_status()
            verified_routes = verified_response.json()
            matches = (
                [
                    route
                    for route in verified_routes
                    if isinstance(route, dict) and route.get("description") == self.route_name
                ]
                if isinstance(verified_routes, list)
                else []
            )
            matched = matches[0] if len(matches) == 1 else {}
            verified_ips = {
                str(entry.get("ip_or_subnet"))
                for entry in matched.get("ip_addresses", [])
                if isinstance(entry, dict)
            }
            if (
                matched.get("enabled") != requested_enabled
                or verified_ips != set(desired_list)
                or matched.get("network_id") != vpn_id
                or matched.get("target_devices") != [{"type": "ALL_CLIENTS"}]
                or (
                    "kill_switch_enabled" in matched
                    and bool(matched.get("kill_switch_enabled")) != self.kill_switch
                )
            ):
                if existing_route:
                    restored = await client.put(
                        f"{routes_url}/{existing_route['_id']}", json=existing_route
                    )
                    restored.raise_for_status()
                elif len(matches) == 1 and matched.get("_id"):
                    deleted = await client.delete(f"{routes_url}/{matched['_id']}")
                    deleted.raise_for_status()
                raise RuntimeError(
                    "UniFi post-write verification failed; previous policy restoration requested"
                )

            now = datetime.now(UTC)
            self._active_routes = effective_desired
            self._observed_kill_switch = (
                bool(matched.get("kill_switch_enabled"))
                if "kill_switch_enabled" in matched
                else self.kill_switch
            )
            self._routes_loaded = True
            self._observed_at = now
            self._last_sync = now
            duration_ms = (time.perf_counter() - start) * 1000.0
            added_count = (
                len(effective_desired - effective_current)
                if desired_cidrs is not None
                else len(to_add)
            )
            removed_count = (
                len(effective_current - effective_desired)
                if desired_cidrs is not None
                else len(to_remove)
            )

            return SyncResult(
                driver_name=self.driver_name,
                added_count=added_count,
                removed_count=removed_count,
                total_active=len(self._active_routes),
                duration_ms=round(duration_ms, 2),
                success=True,
            )

    def get_status(self) -> DriverStatus:
        """Return current in-memory status without querying UniFi over the network."""
        is_configured = bool(self.controller_url and self.api_key)
        recent = self._observed_at is not None and (
            datetime.now(UTC) - self._observed_at
        ).total_seconds() < max(settings.sync_interval_seconds * 3, 60)
        health = DriverHealth.HEALTHY if is_configured and recent else DriverHealth.DEGRADED
        return DriverStatus(
            driver_name=self.driver_name,
            health=health,
            active_routes_count=len(self._active_routes),
            last_sync=self._last_sync,
            details={
                "controller_url": self.controller_url or "not_configured",
                "site": self.site,
                "route_name": self.route_name,
                "vpn_network_id": self.vpn_network_id,
                "kill_switch_enabled": (
                    self._observed_kill_switch
                    if self._observed_kill_switch is not None
                    else self.kill_switch
                ),
                "configured": is_configured,
                "observed_at": self._observed_at.isoformat() if self._observed_at else None,
            },
        )

    async def health_check(self) -> DriverStatus:
        """Inspect UniFi controller connectivity and VPN interfaces on demand."""
        from app.core.service_errors import sanitize_driver_error

        is_configured = bool(self.controller_url and self.api_key)
        vpn_list: list[dict[str, Any]] = []
        route_found = False
        health = DriverHealth.DEGRADED
        ready = False
        needs_vpn_selection = False
        error_msg: str | None = None

        if is_configured:
            try:
                vpn_list = await self.get_vpn_networks()
                curr_routes = await self.get_current_routes(force=True)
                route_found = bool(curr_routes)
                vpn_ids = {v["id"] for v in vpn_list}
                if not vpn_list:
                    health = DriverHealth.DEGRADED
                    error_msg = (
                        "No se encontró ningún cliente VPN en UniFi Network. "
                        "Crea un WireGuard/VPN Client en UniFi primero."
                    )
                elif self.vpn_network_id and self.vpn_network_id not in vpn_ids:
                    health = DriverHealth.UNHEALTHY
                    needs_vpn_selection = True
                    error_msg = (
                        f"La VPN seleccionada ({self.vpn_network_id}) no existe en UniFi. "
                        "Selecciona una VPN válida."
                    )
                elif len(vpn_list) > 1 and not self.vpn_network_id:
                    health = DriverHealth.DEGRADED
                    needs_vpn_selection = True
                    error_msg = (
                        f"Se detectaron {len(vpn_list)} clientes VPN en UniFi. "
                        "Selecciona explícitamente cuál usar antes de sincronizar."
                    )
                else:
                    health = DriverHealth.HEALTHY
                    ready = True
            except Exception as ex:
                logger.warning("UniFi health probe failed: %s", ex)
                health = DriverHealth.UNHEALTHY
                error_msg = sanitize_driver_error(ex, self.driver_name, "probing UniFi controller")

        details: dict[str, Any] = {
            "controller_url": self.controller_url or "not_configured",
            "site": self.site,
            "route_name": self.route_name,
            "vpn_network_id": self.vpn_network_id,
            "available_vpn_networks": len(vpn_list),
            "vpn_networks": vpn_list,
            "needs_vpn_selection": needs_vpn_selection,
            "ready": ready,
            "route_exists_in_unifi": route_found,
            "kill_switch_enabled": (
                self._observed_kill_switch
                if self._observed_kill_switch is not None
                else self.kill_switch
            ),
            "configured": is_configured,
        }
        if error_msg:
            details["error"] = error_msg

        status = DriverStatus(
            driver_name=self.driver_name,
            health=health,
            active_routes_count=len(self._active_routes),
            last_sync=self._last_sync,
            details=details,
        )
        self._last_health_check = datetime.now(UTC)
        self._cached_health_status = status
        return status

    async def close(self) -> None:
        """Release HTTP client sessions."""
        pass

    async def teardown(self) -> None:
        """Delete managed routes and confirm their absence before clearing memory."""
        if not self.controller_url or not self.api_key:
            raise RuntimeError("UniFi controller URL and API key are required")
        async with self._client() as client:
            prefix = await self.discover_prefix(client)
            url = f"{self.controller_url}{prefix}/v2/api/site/{self.site}/trafficroutes"
            response = await client.get(url)
            response.raise_for_status()
            routes = response.json()
            if not isinstance(routes, list):
                raise RuntimeError("Invalid UniFi routes response")
            for route in routes:
                if isinstance(route, dict) and route.get("description") == self.route_name:
                    route_id = route.get("_id")
                    if not route_id:
                        raise RuntimeError("Managed UniFi route has no ID")
                    deleted = await client.delete(f"{url}/{route_id}")
                    deleted.raise_for_status()
            verified = await client.get(url)
            verified.raise_for_status()
            remaining = verified.json()
            if not isinstance(remaining, list) or any(
                isinstance(route, dict) and route.get("description") == self.route_name
                for route in remaining
            ):
                raise RuntimeError("UniFi teardown could not be verified")
        self._active_routes.clear()
        self._observed_kill_switch = None
        self._routes_loaded = False
        self._last_sync = datetime.now(UTC)
