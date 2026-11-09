"""Async boundary around pinned stopliga Omada/OPNsense clients and rollback logic."""

import asyncio
import contextlib
import ipaddress
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from stopliga.models import Config, FeedSnapshot
from stopliga.routers.factory import create_router_driver
from stopliga.utils import stable_hash

from app.core.config import Settings
from app.drivers.base import BaseRouterDriver, DriverHealth, DriverStatus, SyncResult, TargetCIDR


def canonical(values: list[str]) -> set[str]:
    return {str(ipaddress.ip_network(value, strict=False)) for value in values}


async def run_blocking[T](function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Hold ownership until a threaded router request finishes, even during shutdown."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class StopligaAdapter(BaseRouterDriver):
    """Upstream performs router discovery, updates, post-write verification and rollback."""

    def __init__(self, cfg: Settings) -> None:
        self.driver_name = cfg.router_driver
        self.config = Config(
            router_type=cfg.router_driver,
            route_name=cfg.omada_route_name
            if cfg.router_driver == "omada"
            else cfg.opnsense_route_name,
            site=cfg.omada_site,
            omada_base_url=cfg.omada_base_url,
            omada_client_id=cfg.omada_client_id,
            omada_client_secret=cfg.omada_client_secret,
            omada_omadac_id=cfg.omada_controller_id,
            omada_target_type=cfg.omada_target_type,
            omada_target=cfg.omada_target,
            omada_verify_tls=cfg.omada_verify_tls,
            opnsense_host=cfg.opnsense_host,
            opnsense_api_key=cfg.opnsense_api_key,
            opnsense_api_secret=cfg.opnsense_api_secret,
            opnsense_verify_tls=cfg.opnsense_verify_tls,
            opnsense_alias_name=cfg.opnsense_alias_name,
            retries=1,
        )
        self.backend = create_router_driver(self.config)
        self._routes: set[str] = set()
        self._last_sync: datetime | None = None
        self._health = DriverHealth.DEGRADED
        self._error: str | None = None

    async def initialize(self) -> None:
        return None

    def _observe(self) -> set[str]:
        if not self.config.has_router_api_access():
            raise RuntimeError("Router credentials and target must be configured")
        if self.driver_name == "opnsense":
            from stopliga.opnsense import OPNsenseClient, parse_alias_content

            client = OPNsenseClient(self.config)
            client.authenticate()
            rule = client.search_rule(self.config.route_name)
            alias = client.search_alias(self.config.opnsense_alias_name or self.config.route_name)
            if not rule or str(rule.get("enabled")).lower() not in {"true", "1"}:
                return set()
            if not alias:
                raise RuntimeError("Enabled OPNsense rule has no managed alias")
            return canonical(parse_alias_content(client.get_alias_item(str(alias["uuid"]))))
        from stopliga.routers.omada import (
            OmadaClient,
            _flatten_route_destinations,
            _group_destinations,
        )

        client = OmadaClient(self.config)
        site = client.resolve_site()
        routes = client.list_policy_routes(site.site_id)
        matches = [route for route in routes if route.get("name") == self.config.route_name]
        if len(matches) > 1:
            raise RuntimeError("Duplicate managed Omada policy")
        if not matches or matches[0].get("status") is False:
            return set()
        if matches[0].get("status") is not True:
            raise RuntimeError("Omada policy does not expose a boolean status")
        groups = client.list_groups(site.site_id)
        by_id = {str(group["groupId"]): group for group in groups}
        destinations = {key: _group_destinations(group) for key, group in by_id.items()}
        return canonical(_flatten_route_destinations(matches[0], by_id, destinations))

    async def get_current_routes(self, force: bool = False) -> set[str]:
        try:
            self._routes = await run_blocking(self._observe)
            self._health = DriverHealth.HEALTHY
            self._error = None
            return set(self._routes)
        except Exception:
            self._health = DriverHealth.UNHEALTHY
            self._error = "Router observation failed"
            raise

    async def apply_policy(self, targets: set[TargetCIDR], enabled: bool) -> SyncResult:
        start = time.perf_counter()
        try:
            current = await self.get_current_routes(force=True)
            values = sorted({target.cidr for target in targets}) if enabled else []
            snapshot = FeedSnapshot(
                bool(enabled),
                bool(enabled and values),
                values,
                {},
                len(values),
                len(values),
                0,
                [],
                stable_hash(values),
                stable_hash(values),
            )
            await run_blocking(
                self.backend.sync,
                snapshot,
                {},
                guard_writer=lambda *args: None,
                guard_clearer=lambda: None,
            )
            observed = await self.get_current_routes(force=True)

            # Omada collapses networks; compare coverage rather than spelling/count.
            def coverage(cidrs: set[str]) -> list[str]:
                ipv4 = [ipaddress.IPv4Network(value) for value in cidrs if ":" not in value]
                ipv6 = [ipaddress.IPv6Network(value) for value in cidrs if ":" in value]
                return sorted(
                    str(net)
                    for net in [
                        *ipaddress.collapse_addresses(ipv4),
                        *ipaddress.collapse_addresses(ipv6),
                    ]
                )

            if coverage(observed) != coverage(set(values)):
                raise RuntimeError("Router verification failed")
            self._last_sync = datetime.now(UTC)
            return SyncResult(
                self.driver_name,
                len(set(values) - current),
                len(current - set(values)),
                len(observed),
                (time.perf_counter() - start) * 1000,
                True,
            )
        except Exception:
            self._health = DriverHealth.UNHEALTHY
            self._error = "Router policy failed; check connectivity and prerequisites"
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
            {"error": self._error, "adapter": "stopliga-0.1.31"},
        )

    async def health_check(self) -> DriverStatus:
        with contextlib.suppress(Exception):
            await self.get_current_routes(force=True)
        return self.get_status()

    async def teardown(self) -> None:
        result = await self.apply_policy(set(), False)
        if not result.success:
            raise RuntimeError(result.error_message)

    async def close(self) -> None:
        return None
