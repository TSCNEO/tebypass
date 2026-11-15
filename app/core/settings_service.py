"""Runtime settings and router driver diagnostics endpoints."""

import asyncio
import logging
from typing import Any

from app.core.config import Settings, settings
from app.core.driver_configuration import missing_driver_configuration
from app.core.service_errors import ServiceError, sanitize_driver_error
from app.core.settings_store import (
    EDITABLE_KEYS,
    _coerce,
    _is_masked_or_empty,
    masked_dump,
    save_settings,
)
from app.drivers import get_driver, get_router_driver
from app.drivers.unifi import UniFiRouterDriver
from app.models.schemas import RouterPurgeResponse, RouterTestResponse, UniFiVPNNetwork

logger = logging.getLogger("tebypass.api.settings")


def _parse_bool_flag(raw: Any) -> bool:
    return raw is True or (
        isinstance(raw, str) and raw.strip().lower() in {"1", "true", "yes", "on"}
    )


def _build_candidate_settings(payload: dict[str, Any] | None) -> Settings:
    """Overlay unsaved candidate fields onto current settings without persisting."""
    if not payload:
        return settings
    updates: dict[str, Any] = {}
    for key, raw in payload.items():
        if key not in EDITABLE_KEYS or _is_masked_or_empty(key, raw):
            continue
        updates[key] = _coerce(key, raw)
    if not updates:
        return settings
    return Settings.model_validate({**settings.model_dump(), **updates})


async def _trigger_reconciliation_safely() -> None:
    try:
        from app.core.runtime import trigger_reconciliation

        await trigger_reconciliation(force=True)
    except Exception as ex:
        logger.warning("Auto-sync after settings update failed: %s", ex)


async def read_settings() -> dict[str, Any]:
    """Return every editable setting with secret values masked."""
    return masked_dump()


async def update_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist a batch of settings in SQLite and hot-apply them.

    ``confirm_remote_state_unknown`` is a request control, not a stored setting: it lets an
    operator abandon a policy that could not be retired after the router became
    unreachable or was replaced. The unverified state is recorded and surfaced in status.

    ``skip_auto_sync`` suppresses the automatic background reconciliation when the caller
    is about to perform a coordinated explicit sync in the same workflow.
    """
    payload = dict(payload)
    confirm_remote_state_unknown = _parse_bool_flag(
        payload.pop("confirm_remote_state_unknown", False)
    )
    skip_auto_sync = _parse_bool_flag(payload.pop("skip_auto_sync", False))
    try:
        await save_settings(
            payload,
            confirm_remote_state_unknown=confirm_remote_state_unknown,
        )
        if not skip_auto_sync:
            asyncio.create_task(_trigger_reconciliation_safely())
    except ValueError as ex:
        raise ServiceError(status_code=400, detail=str(ex)) from ex
    except RuntimeError as ex:
        raise ServiceError(status_code=502, detail=str(ex)) from ex
    return masked_dump()


async def list_unifi_vpn_networks(
    payload: dict[str, Any] | None = None,
) -> list[UniFiVPNNetwork]:
    """Query the UniFi controller for available VPN Client networks using persisted or candidate access."""
    try:
        candidate = _build_candidate_settings(payload)
    except ValueError as ex:
        raise ServiceError(status_code=400, detail=str(ex)) from ex

    driver = UniFiRouterDriver(candidate if payload else None)
    try:
        networks = await driver.get_vpn_networks()
        return [
            UniFiVPNNetwork(
                id=net["id"],
                name=net["name"],
                type=net.get("type", "wireguard-client"),
            )
            for net in networks
        ]
    except Exception as ex:
        logger.warning("Failed to fetch UniFi VPN networks: %s", ex)
        raise ServiceError(
            status_code=502,
            detail=sanitize_driver_error(ex, "unifi", "listing VPN networks"),
        ) from ex
    finally:
        await driver.close()


async def test_router_driver(
    payload: dict[str, Any] | None = None,
) -> RouterTestResponse:
    """Build a driver with persisted or candidate settings and probe its health without saving or syncing."""
    try:
        candidate = _build_candidate_settings(payload)
    except ValueError as ex:
        return RouterTestResponse(
            healthy=False,
            driver=str((payload or {}).get("router_driver") or settings.router_driver),
            details={"ready": False, "error": str(ex)},
        )

    driver_name = candidate.router_driver
    missing = missing_driver_configuration(candidate)
    if missing:
        return RouterTestResponse(
            healthy=False,
            driver=driver_name,
            details={
                "ready": False,
                "configured": False,
                "error": f"Faltan campos obligatorios para '{driver_name}': {', '.join(missing)}",
            },
        )

    try:
        driver = get_router_driver(candidate if payload else None)
    except ValueError as ex:
        return RouterTestResponse(
            healthy=False,
            driver=driver_name,
            details={"ready": False, "error": str(ex)},
        )

    try:
        await driver.initialize()
        status_report = await driver.health_check()
    except Exception as ex:  # noqa: BLE001 - surfaced to the UI as diagnostics
        logger.warning("Router driver health probe failed: %s", ex)
        return RouterTestResponse(
            healthy=False,
            driver=driver_name,
            details={
                "ready": False,
                "error": sanitize_driver_error(ex, driver_name, "probing router health"),
            },
        )
    finally:
        await driver.close()

    return RouterTestResponse(
        healthy=status_report.health.value == "healthy",
        driver=status_report.driver_name,
        details={
            "health": status_report.health.value,
            "active_routes_count": status_report.active_routes_count,
            **status_report.details,
        },
    )


async def purge_router_routes() -> RouterPurgeResponse:
    """Purge all routes, address-lists, or sets created by TeByPass on the active router."""
    from app.core.engine import acquire_control_lock
    from app.core.policy_ownership import clear_policy_owner, clear_unverified_remote_state
    from app.core.settings_store import _save_settings

    async with acquire_control_lock():
        driver = get_driver()
        driver_name = driver.driver_name
        try:
            await driver.teardown()
            await clear_policy_owner()
            await clear_unverified_remote_state()
            await _save_settings({"routing_enabled": False})
        except Exception as ex:
            logger.warning("Router teardown/purge failed: %s", ex)
            raise ServiceError(status_code=502, detail="Router purge failed") from ex

    return RouterPurgeResponse(
        success=True,
        driver=driver_name,
        message=f"Regla eliminada de {driver_name}. Enrutamiento puesto en pausa.",
    )
