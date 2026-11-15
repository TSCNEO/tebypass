"""Async boundary around pinned stopliga Omada/OPNsense clients and rollback logic."""

import asyncio
import ipaddress
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from stopliga.models import Config, FeedSnapshot
from stopliga.routers.factory import create_router_driver
from stopliga.utils import stable_hash

from app.core.config import Settings
from app.core.service_errors import sanitize_driver_error
from app.drivers.base import BaseRouterDriver, DriverHealth, DriverStatus, SyncResult, TargetCIDR


def canonical(values: list[str]) -> set[str]:
    return {str(ipaddress.ip_network(value, strict=False)) for value in values}


def _extract_opnsense_value(raw: Any) -> str | None:
    """Extract a scalar or currently selected option from an OPNsense API field."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return "1" if raw else "0"
    if isinstance(raw, str | int | float):
        text = str(raw).strip()
        return text
    if isinstance(raw, dict):
        for key, entry in raw.items():
            if isinstance(entry, dict) and str(entry.get("selected", "")).strip().lower() in {
                "1",
                "true",
            }:
                val = entry.get("value")
                return str(val if val is not None else key).strip()
        # Unselected option dictionary -> empty selection.
        return ""
    if isinstance(raw, list):
        return ",".join(str(item).strip() for item in raw if str(item).strip())
    return None


def _is_opnsense_truthy(raw: Any) -> bool:
    val = _extract_opnsense_value(raw)
    if val is None:
        return False
    return val.strip().lower() in {"1", "true", "yes", "on"}


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

    retains_policy_when_disabled = True

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
        self._ready: bool = False
        self._verification: str | None = None
        self._manual_check: str | None = None
        self._rule_enabled: bool | None = None

    async def initialize(self) -> None:
        return None

    def _opnsense_alias_name(self) -> str:
        from stopliga.opnsense import sanitize_alias_name

        return str(self.config.opnsense_alias_name or sanitize_alias_name(self.config.route_name))

    def _fetch_opnsense_rule_details(self, client: Any, rule_row: dict[str, Any]) -> dict[str, Any]:
        merged = dict(rule_row)
        rule_uuid = str(rule_row.get("uuid", "")).strip()
        if not rule_uuid:
            return merged
        try:
            payload = client.request("GET", f"/firewall/filter/getRule/{rule_uuid}")
        except Exception:
            return merged
        if not isinstance(payload, dict):
            return merged
        raw_rule = payload.get("rule")
        detail: dict[str, Any] = raw_rule if isinstance(raw_rule, dict) else payload
        for key, value in detail.items():
            if (
                value is not None and (key not in merged or merged[key] in (None, ""))
            ) or isinstance(value, dict):
                merged[key] = value
        return merged

    def _verify_opnsense_rule_binding(
        self,
        client: Any,
        rule_row: dict[str, Any],
        alias_row: dict[str, Any] | None,
    ) -> tuple[bool, str | None, str | None]:
        alias_name = self._opnsense_alias_name()
        route_name = self.config.route_name
        merged = self._fetch_opnsense_rule_details(client, rule_row)

        action_val = _extract_opnsense_value(merged.get("action"))
        if action_val is not None and action_val != "" and action_val.lower() != "pass":
            raise RuntimeError(
                f"La regla OPNsense '{route_name}' tiene acción '{action_val}', debe ser 'pass'."
            )

        direction_val = _extract_opnsense_value(merged.get("direction"))
        if direction_val is not None and direction_val.lower() == "out":
            raise RuntimeError(
                f"La regla OPNsense '{route_name}' tiene dirección 'out'; debe aplicarse al tráfico entrante ('in')."
            )

        negated_raw = merged.get("destination_not", merged.get("dstnot"))
        if negated_raw is not None and _is_opnsense_truthy(negated_raw):
            raise RuntimeError(
                f"La regla OPNsense '{route_name}' tiene negado el destino (destination_not); "
                f"debe coincidir directamente con el alias '{alias_name}'."
            )

        dest_val = None
        for key in ("destination_net", "destination", "dst"):
            if key in merged and merged[key] is not None:
                dest_val = _extract_opnsense_value(merged[key])
                if dest_val is not None:
                    break
        allowed_targets = {alias_name.lower()}
        if alias_row and alias_row.get("uuid"):
            allowed_targets.add(str(alias_row["uuid"]).strip().lower())
        if dest_val is not None and dest_val != "" and dest_val.lower() not in allowed_targets:
            raise RuntimeError(
                f"La regla OPNsense '{route_name}' apunta al destino '{dest_val}' en lugar del alias '{alias_name}'."
            )

        gateway_present = "gateway" in merged and merged.get("gateway") is not None
        gateway_val = _extract_opnsense_value(merged.get("gateway")) if gateway_present else None
        if gateway_present and (not gateway_val or gateway_val.lower() in {"default", "none"}):
            raise RuntimeError(
                f"La regla OPNsense '{route_name}' no tiene un gateway VPN asignado (gateway='{gateway_val or 'default'}')."
            )

        missing: list[str] = []
        if action_val is None or action_val == "":
            missing.append("action")
        if dest_val is None or dest_val == "":
            missing.append("destination")
        if negated_raw is None:
            missing.append("destination_not")
        if not gateway_present:
            missing.append("gateway")

        if missing:
            manual = (
                f"Verificación manual en OPNsense (Firewall > Rules): confirma que la regla "
                f"'{route_name}' tiene Action=Pass, Destination={alias_name} (sin invertir) y "
                f"Gateway=<tu_gateway_VPN> (no Default)."
            )
            warning = (
                f"Verificación parcial de OPNsense: la respuesta del firewall omitió "
                f"({', '.join(missing)}). {manual}"
            )
            return False, warning, manual
        return True, None, None

    def _inspect_opnsense(self, *, require_rule: bool) -> set[str]:
        from stopliga.opnsense import OPNsenseClient, parse_alias_content

        client = OPNsenseClient(self.config)
        client.authenticate()
        alias_name = self._opnsense_alias_name()
        rule = client.search_rule(self.config.route_name)
        alias = client.search_alias(alias_name)

        if rule is None:
            self._rule_enabled = None
            self._verification = None
            self._manual_check = None
            if require_rule:
                raise RuntimeError(
                    f"Regla '{self.config.route_name}' no encontrada en OPNsense. "
                    f"Créala en Firewall > Rules apuntando al alias '{alias_name}' y al gateway VPN."
                )
            return set()

        rule_enabled = str(rule.get("enabled")).lower() in {"true", "1"}
        self._rule_enabled = rule_enabled
        full, warning, manual = self._verify_opnsense_rule_binding(client, rule, alias)
        self._verification = "full" if full else "partial"
        self._manual_check = manual
        self._error = warning

        if not rule_enabled:
            return set()
        if not alias:
            raise RuntimeError("Enabled OPNsense rule has no managed alias")
        return canonical(parse_alias_content(client.get_alias_item(str(alias["uuid"]))))

    def _inspect_omada(self, *, verify_prerequisites: bool) -> set[str]:
        from stopliga.routers.omada import (
            OmadaClient,
            _flatten_route_destinations,
            _group_destinations,
        )

        client = OmadaClient(self.config)
        site = client.resolve_site()
        if verify_prerequisites:
            if hasattr(self.backend, "_resolve_source_network_ids"):
                self.backend._resolve_source_network_ids(client, site.site_id)
            if hasattr(self.backend, "_resolve_target"):
                self.backend._resolve_target(client, site.site_id)
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

    def _observe(self) -> set[str]:
        if not self.config.has_router_api_access():
            raise RuntimeError("Router credentials and target must be configured")
        if self.driver_name == "opnsense":
            return self._inspect_opnsense(require_rule=False)
        return self._inspect_omada(verify_prerequisites=False)

    def _verify_readiness(self) -> set[str]:
        if not self.config.has_router_api_access():
            raise RuntimeError("Router credentials and target must be configured")
        if self.driver_name == "opnsense":
            return self._inspect_opnsense(require_rule=True)
        return self._inspect_omada(verify_prerequisites=True)

    async def get_current_routes(self, force: bool = False) -> set[str]:
        try:
            self._routes = await run_blocking(self._observe)
            self._health = (
                DriverHealth.DEGRADED if self._verification == "partial" else DriverHealth.HEALTHY
            )
            if self._verification != "partial":
                self._error = None
            return set(self._routes)
        except Exception as ex:
            self._health = DriverHealth.UNHEALTHY
            self._ready = False
            self._error = sanitize_driver_error(ex, self.driver_name, "observing router state")
            raise

    def _mark_policy_write_started(self) -> None:
        self.policy_write_started = True

    async def apply_policy(self, targets: set[TargetCIDR], enabled: bool) -> SyncResult:
        start = time.perf_counter()
        custom_observe = (
            "_observe" in self.__dict__
            or type(self)._observe is not _ORIGINAL_OBSERVE
            or "get_current_routes" in self.__dict__
            or type(self).get_current_routes is not _ORIGINAL_GET_CURRENT_ROUTES
        )
        try:
            if self.driver_name == "opnsense" and not custom_observe and enabled and targets:
                await run_blocking(self._inspect_opnsense, require_rule=True)
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
                before_write=self._mark_policy_write_started,
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
            self._ready = True
            return SyncResult(
                self.driver_name,
                len(set(values) - current),
                len(current - set(values)),
                len(observed),
                (time.perf_counter() - start) * 1000,
                True,
            )
        except Exception as ex:
            self._health = DriverHealth.UNHEALTHY
            self._ready = False
            self._error = sanitize_driver_error(ex, self.driver_name, "applying policy")
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
        details: dict[str, Any] = {
            "error": self._error,
            "adapter": "stopliga-0.1.31",
            "ready": self._ready,
        }
        if self._verification is not None:
            details["verification"] = self._verification
        if self._manual_check is not None:
            details["manual_check"] = self._manual_check
        if self._rule_enabled is not None:
            details["rule_enabled"] = self._rule_enabled
            if self._rule_enabled is False and self._ready:
                details["note"] = (
                    "La regla existe y es compatible (actualmente deshabilitada/en pausa); "
                    "la sincronización la activará automáticamente."
                )
        return DriverStatus(
            self.driver_name,
            self._health,
            len(self._routes),
            self._last_sync,
            details,
        )

    async def health_check(self) -> DriverStatus:
        custom_observe = (
            "_observe" in self.__dict__
            or type(self)._observe is not _ORIGINAL_OBSERVE
            or "get_current_routes" in self.__dict__
            or type(self).get_current_routes is not _ORIGINAL_GET_CURRENT_ROUTES
        )
        try:
            self._routes = await run_blocking(
                self._observe if custom_observe else self._verify_readiness
            )
            self._ready = True
            if self._verification == "partial":
                self._health = DriverHealth.DEGRADED
            else:
                self._health = DriverHealth.HEALTHY
                self._error = None
        except Exception as ex:
            self._health = DriverHealth.UNHEALTHY
            self._ready = False
            self._error = sanitize_driver_error(ex, self.driver_name, "probing readiness")
        return self.get_status()

    async def teardown(self) -> None:
        result = await self.apply_policy(set(), False)
        if not result.success:
            raise RuntimeError(result.error_message)

    async def close(self) -> None:
        return None


_ORIGINAL_OBSERVE = StopligaAdapter._observe
_ORIGINAL_GET_CURRENT_ROUTES = StopligaAdapter.get_current_routes
