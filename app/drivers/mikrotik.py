"""RouterOS v7 REST policy rules into an administrator-provisioned routing table."""

import ipaddress
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import Settings, settings
from app.drivers.base import BaseRouterDriver, DriverHealth, DriverStatus, SyncResult, TargetCIDR


class MikroTikRouterDriver(BaseRouterDriver):
    driver_name = "mikrotik"

    def __init__(self, cfg: Settings | None = None) -> None:
        cfg = cfg or settings
        self.api_url = (cfg.mikrotik_api_url or "").rstrip("/")
        if self.api_url and not self.api_url.endswith("/rest"):
            self.api_url += "/rest"
        self.username = cfg.mikrotik_username or ""
        self.password = cfg.mikrotik_password or ""
        self.table = cfg.mikrotik_routing_table
        self.owner = f"{cfg.mikrotik_address_list}:managed"
        self.verify_tls = cfg.mikrotik_verify_tls
        self._routes: set[str] = set()
        self._health = DriverHealth.DEGRADED
        self._last_sync: datetime | None = None
        self._error: str | None = None

    def _client(self) -> httpx.AsyncClient:
        if not self.api_url or not self.username or not self.password or not self.table:
            raise ValueError("RouterOS URL, credentials and routing table are required")
        return httpx.AsyncClient(
            base_url=self.api_url + "/",
            auth=(self.username, self.password),
            verify=self.verify_tls,
            timeout=15,
        )

    async def initialize(self) -> None:
        return None

    async def _records(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        response = await client.get("routing/rule", params={"comment": self.owner})
        response.raise_for_status()
        records = response.json()
        if not isinstance(records, list):
            raise RuntimeError("Invalid RouterOS rule response")
        return [
            item for item in records if isinstance(item, dict) and item.get("comment") == self.owner
        ]

    async def get_current_routes(self, force: bool = False) -> set[str]:
        async with self._client() as client:
            records = await self._records(client)
        self._routes = {
            str(ipaddress.ip_network(item["dst-address"], strict=False))
            for item in records
            if item.get("disabled", "false") == "false" and item.get("table") == self.table
        }
        return set(self._routes)

    async def apply_policy(self, targets: set[TargetCIDR], enabled: bool) -> SyncResult:
        start = time.perf_counter()
        desired = (
            {str(ipaddress.ip_network(target.cidr, strict=False)) for target in targets}
            if enabled
            else set()
        )
        if any(ipaddress.ip_network(value).version != 4 for value in desired):
            raise ValueError("RouterOS MVP supports IPv4 only")
        try:
            async with self._client() as client:
                tables = await client.get("routing/table", params={"name": self.table})
                tables.raise_for_status()
                if not any(
                    item.get("name") == self.table and str(item.get("fib")).lower() == "true"
                    for item in tables.json()
                ):
                    raise ValueError("Prepare a FIB routing table with a VPN default route first")
                records = await self._records(client)
                current = {
                    str(ipaddress.ip_network(item["dst-address"], strict=False)) for item in records
                }
                keep: set[str] = set()
                for item in records:
                    value = str(ipaddress.ip_network(item["dst-address"], strict=False))
                    if value not in desired or value in keep:
                        response = await client.delete(f"routing/rule/{item['.id']}")
                    else:
                        response = await client.patch(
                            f"routing/rule/{item['.id']}",
                            json={
                                "table": self.table,
                                "action": "lookup-only-in-table",
                                "disabled": "false",
                            },
                        )
                        keep.add(value)
                    response.raise_for_status()
                for value in sorted(desired - keep):
                    response = await client.put(
                        "routing/rule",
                        json={
                            "dst-address": value,
                            "table": self.table,
                            "action": "lookup-only-in-table",
                            "disabled": "false",
                            "comment": self.owner,
                        },
                    )
                    response.raise_for_status()
                verified = await self._records(client)
                actual = {
                    str(ipaddress.ip_network(item["dst-address"], strict=False))
                    for item in verified
                    if item.get("table") == self.table
                    and item.get("action") == "lookup-only-in-table"
                    and item.get("disabled", "false") == "false"
                }
                if actual != desired or len(verified) != len(desired):
                    raise RuntimeError("RouterOS policy verification failed")
            self._routes = actual
            self._health = DriverHealth.HEALTHY
            self._error = None
            self._last_sync = datetime.now(UTC)
            return SyncResult(
                self.driver_name,
                len(desired - current),
                len(current - desired),
                len(actual),
                (time.perf_counter() - start) * 1000,
                True,
            )
        except Exception:
            self._health = DriverHealth.UNHEALTHY
            self._error = "RouterOS update failed; state may be partial, retry reconciliation"
            return SyncResult(
                self.driver_name, 0, 0, -1, (time.perf_counter() - start) * 1000, False, self._error
            )

    async def sync_routes(self, to_add: set[TargetCIDR], to_remove: set[str]) -> SyncResult:
        current = await self.get_current_routes(force=True)
        desired = (current - to_remove) | {target.cidr for target in to_add}
        return await self.apply_policy(
            {TargetCIDR(value, "managed") for value in desired}, bool(desired)
        )

    def get_status(self) -> DriverStatus:
        return DriverStatus(
            self.driver_name,
            self._health,
            len(self._routes),
            self._last_sync,
            {"error": self._error, "routing_table": self.table},
        )

    async def health_check(self) -> DriverStatus:
        try:
            await self.get_current_routes(force=True)
            self._health = DriverHealth.HEALTHY
            self._error = None
        except Exception:
            self._health = DriverHealth.UNHEALTHY
            self._error = "RouterOS observation failed"
        return self.get_status()

    async def teardown(self) -> None:
        result = await self.apply_policy(set(), False)
        if not result.success:
            raise RuntimeError(result.error_message)

    async def close(self) -> None:
        return None
