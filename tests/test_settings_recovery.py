"""C01 regressions: candidate configuration, policy ownership and explicit recovery."""

import asyncio
import json
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings, settings
from app.core.driver_configuration import driver_identity
from app.core.engine import ReconciliationEngine
from app.core.policy_ownership import (
    clear_policy_owner,
    latest_applied_policy,
    read_policy_owner,
    read_unverified_remote_state,
    record_policy_owner,
    record_unverified_remote_state,
)
from app.core.settings_store import load_into_settings, save_settings
from app.db.models import AppSetting, SyncEvent, Target
from app.db.session import AsyncSessionLocal
from app.drivers import invalidate_driver
from app.drivers.base import (
    BaseRouterDriver,
    DriverHealth,
    DriverStatus,
    SyncResult,
    TargetCIDR,
)
from app.drivers.mikrotik import MikroTikRouterDriver
from app.drivers.stopliga_adapter import StopligaAdapter
from app.drivers.unifi import UniFiRouterDriver
from app.drivers.virtual_gateway import VirtualGatewayDriver

UNIFI_URL = "https://unifi.example.net"
REPAIRED_URL = "https://198.51.100.2"
API_KEY = "placeholder-api-key"


def _select_driver(name: str) -> None:
    """Set the in-memory driver without persisting it."""
    settings.router_driver = name  # type: ignore[assignment]


def _select_mock() -> None:
    """Start from a clean Mock selection regardless of earlier tests."""
    _select_driver("mock")
    settings.resolve_ipv6 = False
    settings.unifi_controller_url = None
    settings.unifi_api_key = None


def _clear_driver_credentials(driver: str) -> None:
    """Remove the required values of a driver so activation must be rejected."""
    for key in {
        "mikrotik": (
            "mikrotik_api_url",
            "mikrotik_username",
            "mikrotik_password",
            "mikrotik_routing_table",
        ),
        "omada": (
            "omada_base_url",
            "omada_client_id",
            "omada_client_secret",
            "omada_controller_id",
            "omada_target",
        ),
        "opnsense": ("opnsense_host", "opnsense_api_key", "opnsense_api_secret"),
    }[driver]:
        setattr(settings, key, "")


def _select_complete_unifi() -> None:
    """Configure a complete UniFi candidate directly in memory (the DB is not touched)."""
    _select_driver("unifi")
    settings.unifi_controller_url = UNIFI_URL
    settings.unifi_api_key = API_KEY
    settings.unifi_site = "default"
    settings.unifi_route_name = "TeByPass"


async def test_incomplete_driver_activation_is_rejected(client: httpx.AsyncClient) -> None:
    """Selecting a driver without its required connection values must not persist it."""
    _select_mock()
    response = await client.put("/api/v1/settings", json={"router_driver": "unifi"})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "unifi_controller_url" in detail
    assert "unifi_api_key" in detail
    assert settings.router_driver == "mock"
    async with AsyncSessionLocal() as session:
        assert await session.get(AppSetting, "router_driver") is None


@pytest.mark.parametrize(
    ("driver", "missing"),
    [
        ("mikrotik", "mikrotik_api_url"),
        ("omada", "omada_base_url"),
        ("opnsense", "opnsense_host"),
    ],
)
async def test_incomplete_activation_rejects_every_remote_driver(
    client: httpx.AsyncClient, driver: str, missing: str
) -> None:
    """Required connection values are validated before persisting any driver."""
    _select_mock()
    _clear_driver_credentials(driver)

    response = await client.put("/api/v1/settings", json={"router_driver": driver})

    assert response.status_code == 400
    assert missing in response.json()["detail"]
    assert settings.router_driver == "mock"


async def test_complete_activation_is_a_candidate_not_an_applied_policy(
    client: httpx.AsyncClient,
) -> None:
    """A complete activation from Mock needs no teardown and records no ownership."""
    _select_mock()
    teardown = AsyncMock()
    with (
        patch.object(UniFiRouterDriver, "teardown", teardown),
        patch("app.core.settings_service._trigger_reconciliation_safely", AsyncMock()),
    ):
        response = await client.put(
            "/api/v1/settings",
            json={
                "router_driver": "unifi",
                "unifi_controller_url": UNIFI_URL,
                "unifi_api_key": API_KEY,
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["unifi_api_key"] == "***saved***"
    assert settings.router_driver == "unifi"
    teardown.assert_not_awaited()
    assert await read_policy_owner() is None


async def test_unapplied_candidate_can_be_corrected_and_abandoned(
    client: httpx.AsyncClient,
) -> None:
    """URL repair and the return to Mock must work while no policy was ever applied."""
    _select_mock()
    async with AsyncSessionLocal() as session:
        session.add(Target(value="198.51.100.7/32", destination_type="cidr"))
        await session.commit()

    teardown = AsyncMock()
    with (
        patch.object(UniFiRouterDriver, "teardown", teardown),
        patch("app.core.settings_service._trigger_reconciliation_safely", AsyncMock()),
    ):
        activated = await client.put(
            "/api/v1/settings",
            json={
                "router_driver": "unifi",
                "unifi_controller_url": UNIFI_URL,
                "unifi_api_key": API_KEY,
            },
        )
        repaired = await client.put("/api/v1/settings", json={"unifi_controller_url": REPAIRED_URL})
        abandoned = await client.put("/api/v1/settings", json={"router_driver": "mock"})

    assert [activated.status_code, repaired.status_code, abandoned.status_code] == [200, 200, 200]
    assert settings.router_driver == "mock"
    assert settings.unifi_controller_url == REPAIRED_URL
    teardown.assert_not_awaited()
    async with AsyncSessionLocal() as session:
        assert (await session.execute(select(Target.value))).scalars().all() == ["198.51.100.7/32"]


async def test_previously_saved_incomplete_driver_can_be_repaired(
    client: httpx.AsyncClient,
) -> None:
    """A database left incomplete by an older release must remain recoverable."""
    _select_driver("unifi")
    settings.unifi_controller_url = None
    settings.unifi_api_key = None

    teardown = AsyncMock()
    with (
        patch.object(UniFiRouterDriver, "teardown", teardown),
        patch("app.core.settings_service._trigger_reconciliation_safely", AsyncMock()),
    ):
        repaired = await client.put("/api/v1/settings", json={"unifi_controller_url": UNIFI_URL})
        abandoned = await client.put("/api/v1/settings", json={"router_driver": "mock"})

    assert [repaired.status_code, abandoned.status_code] == [200, 200]
    assert settings.router_driver == "mock"
    teardown.assert_not_awaited()


async def test_incomplete_legacy_driver_can_be_repaired_even_with_sync_history(
    client: httpx.AsyncClient,
) -> None:
    """Old sync history must not re-create the deadlock for a provably incomplete candidate."""
    _select_driver("unifi")
    settings.unifi_controller_url = None
    settings.unifi_api_key = None
    async with AsyncSessionLocal() as session:
        session.add(
            SyncEvent(
                driver_name="unifi",
                success=True,
                total_active=1,
                executed_at=datetime.now(UTC),
            )
        )
        await session.commit()
    assert await latest_applied_policy("unifi")

    teardown = AsyncMock()
    with (
        patch.object(UniFiRouterDriver, "teardown", teardown),
        patch("app.core.settings_service._trigger_reconciliation_safely", AsyncMock()),
    ):
        repaired = await client.put("/api/v1/settings", json={"unifi_controller_url": UNIFI_URL})

    assert repaired.status_code == 200
    teardown.assert_not_awaited()


async def test_owned_policy_blocks_identity_change_when_retirement_fails(
    client: httpx.AsyncClient,
) -> None:
    """A recorded owner must not be silently orphaned by persisting a new identity."""
    _select_complete_unifi()
    await record_policy_owner(settings)

    with patch.object(
        UniFiRouterDriver, "teardown", AsyncMock(side_effect=RuntimeError("offline"))
    ):
        response = await client.put("/api/v1/settings", json={"unifi_controller_url": REPAIRED_URL})

    assert response.status_code == 502
    assert "confirm_remote_state_unknown=true" in response.json()["detail"]
    assert settings.unifi_controller_url == UNIFI_URL
    assert await read_policy_owner() is not None


async def test_verified_retirement_clears_owner_before_persisting(
    client: httpx.AsyncClient,
) -> None:
    """A successful teardown allows the identity change and clears the record."""
    _select_complete_unifi()
    await record_policy_owner(settings)
    teardown = AsyncMock()
    with (
        patch.object(UniFiRouterDriver, "teardown", teardown),
        patch("app.core.settings_service._trigger_reconciliation_safely", AsyncMock()),
    ):
        response = await client.put("/api/v1/settings", json={"unifi_controller_url": REPAIRED_URL})

    assert response.status_code == 200, response.text
    teardown.assert_awaited_once()
    assert settings.unifi_controller_url == REPAIRED_URL
    assert await read_policy_owner() is None


async def test_explicit_acknowledgement_records_unverified_remote_state(
    client: httpx.AsyncClient,
) -> None:
    """The only way past a failed retirement is a deliberate acknowledgement."""
    _select_complete_unifi()
    await record_policy_owner(settings)

    with patch.object(
        UniFiRouterDriver, "teardown", AsyncMock(side_effect=RuntimeError("router replaced"))
    ):
        response = await client.put(
            "/api/v1/settings",
            json={
                "unifi_controller_url": REPAIRED_URL,
                "confirm_remote_state_unknown": True,
            },
        )

    assert response.status_code == 200, response.text
    assert settings.unifi_controller_url == REPAIRED_URL
    assert await read_policy_owner() is None
    unverified = await read_unverified_remote_state()
    assert unverified is not None
    assert unverified["state"] == "unverified"

    status = await client.get("/api/v1/status")
    assert status.status_code == 200
    assert status.json()["remote_policy_state"] == "unverified"
    assert status.json()["status"] == "degraded"


async def test_legacy_applied_policy_is_not_silently_skipped(client: httpx.AsyncClient) -> None:
    """Upgraded installations without an owner record still require retirement."""
    _select_complete_unifi()
    async with AsyncSessionLocal() as session:
        session.add(
            SyncEvent(
                driver_name="unifi",
                success=True,
                total_active=1,
                executed_at=datetime.now(UTC),
            )
        )
        await session.commit()

    with patch.object(
        UniFiRouterDriver, "teardown", AsyncMock(side_effect=RuntimeError("offline"))
    ):
        response = await client.put("/api/v1/settings", json={"unifi_controller_url": REPAIRED_URL})

    assert response.status_code == 502
    assert settings.unifi_controller_url == UNIFI_URL


async def test_acknowledged_unknown_state_does_not_block_further_repairs(
    client: httpx.AsyncClient,
) -> None:
    """An explicit acknowledgement persists until a verified operation clears it."""
    _select_complete_unifi()
    await record_unverified_remote_state("unifi", "router replaced")
    async with AsyncSessionLocal() as session:
        session.add(
            SyncEvent(
                driver_name="unifi",
                success=True,
                total_active=1,
                executed_at=datetime.now(UTC),
            )
        )
        await session.commit()

    teardown = AsyncMock()
    with (
        patch.object(UniFiRouterDriver, "teardown", teardown),
        patch("app.core.settings_service._trigger_reconciliation_safely", AsyncMock()),
    ):
        response = await client.put("/api/v1/settings", json={"unifi_controller_url": REPAIRED_URL})

    assert response.status_code == 200, response.text
    assert settings.unifi_controller_url == REPAIRED_URL
    teardown.assert_not_awaited()
    assert await read_unverified_remote_state() is not None


class _RecordingDriver(BaseRouterDriver):
    """Minimal remote driver that exposes whether a write started."""

    driver_name = "unifi"

    def __init__(self, *, fail: bool = False, write: bool = True) -> None:
        self.fail = fail
        self.write = write
        self.policy_write_started = False

    async def initialize(self) -> None:
        return None

    async def get_current_routes(self, force: bool = False) -> set[str]:
        return set()

    async def sync_routes(self, to_add: set[TargetCIDR], to_remove: set[str]) -> SyncResult:
        return SyncResult(self.driver_name, 0, 0, 0, 0.0, True)

    async def apply_policy(self, targets: set[TargetCIDR], enabled: bool) -> SyncResult:
        if self.fail:
            self.policy_write_started = self.write
            raise RuntimeError("injected failure")
        self.policy_write_started = True
        return SyncResult(self.driver_name, 1, 0, 1, 0.0, True)

    async def health_check(self) -> DriverStatus:
        return DriverStatus(self.driver_name, DriverHealth.HEALTHY, 1)

    async def close(self) -> None:
        return None

    async def teardown(self) -> None:
        return None


class _DisabledRetainingDriver(_RecordingDriver):
    """Backend that keeps a disabled rule when the desired policy is empty."""

    retains_policy_when_disabled = True

    async def apply_policy(self, targets: set[TargetCIDR], enabled: bool) -> SyncResult:
        self.policy_write_started = True
        return SyncResult(self.driver_name, 0, 1, 0, 0.0, True)


class _EmptyClearingDriver(_RecordingDriver):
    """Backend with no remote configuration left after an empty policy."""

    async def apply_policy(self, targets: set[TargetCIDR], enabled: bool) -> SyncResult:
        return SyncResult(self.driver_name, 0, 0, 0, 0.0, True)


async def _reconcile_with(driver: BaseRouterDriver) -> Any:
    async with AsyncSessionLocal() as session:
        session.add(Target(value="198.51.100.7/32", destination_type="cidr"))
        await session.commit()
    _select_complete_unifi()
    with patch("app.core.engine.get_driver", return_value=driver):
        return await ReconciliationEngine().reconcile()


async def test_engine_records_owner_after_successful_apply(db_ready: None) -> None:
    """A successful remote policy application records the configuration identity."""
    result = await _reconcile_with(_RecordingDriver())

    assert result.success
    owner = await read_policy_owner()
    assert owner is not None
    assert owner.identity == driver_identity(settings)
    assert owner.possible_partial is False


async def test_engine_records_possible_partial_write_on_failure(db_ready: None) -> None:
    """A failure after the write boundary must keep ownership for the next teardown."""
    result = await _reconcile_with(_RecordingDriver(fail=True))

    assert not result.success
    owner = await read_policy_owner()
    assert owner is not None
    assert owner.possible_partial is True


async def test_engine_failure_before_write_does_not_claim_ownership(db_ready: None) -> None:
    """An unreachable controller must not become a deadlock for its own repair."""
    result = await _reconcile_with(_RecordingDriver(fail=True, write=False))

    assert not result.success
    assert await read_policy_owner() is None


async def test_engine_keeps_owner_for_disabled_retained_policy(db_ready: None) -> None:
    """A backend that keeps a disabled rule still owns remote configuration."""
    result = await _reconcile_with(_DisabledRetainingDriver())

    assert result.success
    assert result.total_active == 0
    assert await read_policy_owner() is not None


async def test_engine_clears_owner_after_verified_empty_policy(db_ready: None) -> None:
    """A verified empty policy without retained configuration clears ownership and warnings."""
    _select_complete_unifi()
    await record_policy_owner(settings)
    await record_unverified_remote_state("unifi", "previous failure")
    result = await _reconcile_with(_EmptyClearingDriver())

    assert result.success
    assert result.total_active == 0
    assert await read_policy_owner() is None
    assert await read_unverified_remote_state() is None


async def test_unifi_marks_write_boundary_before_remote_mutation() -> None:
    """The real UniFi driver flags a write only when it is about to mutate the router."""
    driver = UniFiRouterDriver(
        Settings(
            router_driver="unifi",
            unifi_controller_url=UNIFI_URL,
            unifi_api_key=API_KEY,
            unifi_vpn_network_id="vpn-example",
        )
    )
    route: dict[str, Any] = {
        "_id": "route-example",
        "description": "TeByPass",
        "enabled": True,
        "network_id": "vpn-example",
        "ip_addresses": [],
        "target_devices": [{"type": "ALL_CLIENTS"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[route])
        return httpx.Response(500, text="injected failure")

    with (
        patch.object(
            driver,
            "_client",
            lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ),
        pytest.raises(RuntimeError, match="Failed to update"),
    ):
        await driver.apply_policy({TargetCIDR("198.51.100.7/32", "example")}, True)

    assert driver.policy_write_started is True


async def test_unifi_incomplete_config_fails_before_the_write_boundary() -> None:
    """Missing credentials cannot have written anything, so repair stays possible."""
    driver = UniFiRouterDriver(
        Settings(router_driver="unifi", unifi_controller_url=None, unifi_api_key=None)
    )

    with pytest.raises(RuntimeError, match="URL and API key"):
        await driver.apply_policy({TargetCIDR("198.51.100.7/32", "example")}, True)

    assert driver.policy_write_started is False


async def test_save_settings_keeps_reserved_control_out_of_persistence(
    client: httpx.AsyncClient,
) -> None:
    """The recovery control is consumed by the API and never stored as a setting."""
    response = await client.put(
        "/api/v1/settings",
        json={"routing_enabled": False, "confirm_remote_state_unknown": True},
    )

    assert response.status_code == 200
    async with AsyncSessionLocal() as session:
        assert await session.get(AppSetting, "confirm_remote_state_unknown") is None
    await save_settings({"sync_interval_seconds": 0})


async def test_clean_retirement_to_mock_does_not_block_later_unifi_typo(
    client: httpx.AsyncClient,
) -> None:
    """Historical SyncEvents must not deadlock a fresh remote candidate after a clean retirement."""
    _select_mock()
    async with AsyncSessionLocal() as session:
        session.add(Target(value="198.51.100.7/32", destination_type="cidr"))
        await session.commit()

    with patch("app.core.settings_service._trigger_reconciliation_safely", AsyncMock()):
        activated = await client.put(
            "/api/v1/settings",
            json={
                "router_driver": "unifi",
                "unifi_controller_url": UNIFI_URL,
                "unifi_api_key": API_KEY,
                "unifi_vpn_network_id": "vpn-example",
            },
        )
    assert activated.status_code == 200

    route: dict[str, Any] = {
        "_id": "route-example",
        "description": "TeByPass",
        "enabled": True,
        "network_id": "vpn-example",
        "ip_addresses": [{"ip_or_subnet": "198.51.100.7/32"}],
        "target_devices": [{"type": "ALL_CLIENTS"}],
    }

    def ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[route])

    with patch.object(
        UniFiRouterDriver,
        "_client",
        lambda self: httpx.AsyncClient(transport=httpx.MockTransport(ok_handler)),
    ):
        applied = await ReconciliationEngine().reconcile()
    assert applied.success

    with (
        patch.object(UniFiRouterDriver, "teardown", AsyncMock()) as teardown_ok,
        patch("app.core.settings_service._trigger_reconciliation_safely", AsyncMock()),
    ):
        to_mock = await client.put("/api/v1/settings", json={"router_driver": "mock"})
    assert to_mock.status_code == 200
    teardown_ok.assert_awaited_once()
    assert await read_policy_owner() is None

    with patch("app.core.settings_service._trigger_reconciliation_safely", AsyncMock()):
        typo_activate = await client.put(
            "/api/v1/settings",
            json={
                "router_driver": "unifi",
                "unifi_controller_url": "https://unifi-typo.invalid",
                "unifi_api_key": API_KEY,
            },
        )
    assert typo_activate.status_code == 200

    with (
        patch.object(
            UniFiRouterDriver,
            "teardown",
            AsyncMock(side_effect=RuntimeError("DNS NXDOMAIN")),
        ) as teardown_skipped,
        patch("app.core.settings_service._trigger_reconciliation_safely", AsyncMock()),
    ):
        repaired = await client.put("/api/v1/settings", json={"unifi_controller_url": REPAIRED_URL})
        back_to_mock = await client.put("/api/v1/settings", json={"router_driver": "mock"})

    assert [repaired.status_code, back_to_mock.status_code] == [200, 200]
    teardown_skipped.assert_not_awaited()
    assert settings.router_driver == "mock"


async def test_stopliga_before_write_prevents_false_ownership_and_preserves_disabled_rule(
    db_ready: None,
) -> None:
    """StopligaAdapter marks write_started only on before_write and preserves disabled rules."""
    _select_mock()
    await save_settings(
        {
            "router_driver": "opnsense",
            "opnsense_host": "fw.example.net",
            "opnsense_api_key": "key",
            "opnsense_api_secret": "secret",
            "opnsense_route_name": "TeByPass",
        }
    )

    # 1. Empty no-op cycle (no before_write callback) must not claim ownership.
    with (
        patch.object(StopligaAdapter, "get_current_routes", AsyncMock(return_value=set())),
        patch("stopliga.routers.opnsense.OPNsenseRouterDriver.sync", return_value=None),
    ):
        empty_res = await ReconciliationEngine().reconcile()
    assert empty_res.success
    assert await read_policy_owner() is None

    # 2. Pre-flight failure before before_write must not claim partial ownership.
    async with AsyncSessionLocal() as session:
        session.add(Target(value="198.51.100.7/32", destination_type="cidr"))
        await session.commit()

    with (
        patch.object(StopligaAdapter, "get_current_routes", AsyncMock(return_value=set())),
        patch(
            "stopliga.routers.opnsense.OPNsenseRouterDriver.sync",
            side_effect=RuntimeError("Firewall rule not found"),
        ),
    ):
        preflight_fail = await ReconciliationEngine().reconcile()
    assert not preflight_fail.success
    assert await read_policy_owner() is None

    # 3. Real write triggers before_write and records ownership; a later empty no-op retains it.
    def sync_with_write(
        self: Any,
        snapshot: Any,
        meta: Any,
        *,
        before_write: Any = None,
        **kwargs: Any,
    ) -> None:
        if before_write is not None:
            before_write()

    with (
        patch.object(
            StopligaAdapter,
            "get_current_routes",
            AsyncMock(side_effect=[set(), {"198.51.100.7/32"}]),
        ),
        patch("stopliga.routers.opnsense.OPNsenseRouterDriver.sync", sync_with_write),
    ):
        applied = await ReconciliationEngine().reconcile()
    assert applied.success
    assert applied.total_active == 1
    assert await read_policy_owner() is not None

    async with AsyncSessionLocal() as session:
        for target in (await session.execute(select(Target))).scalars().all():
            await session.delete(target)
        await session.commit()

    with (
        patch.object(StopligaAdapter, "get_current_routes", AsyncMock(return_value=set())),
        patch("stopliga.routers.opnsense.OPNsenseRouterDriver.sync", return_value=None),
    ):
        disabled_noop = await ReconciliationEngine().reconcile()
    assert disabled_noop.success
    assert disabled_noop.total_active == 0
    assert await read_policy_owner() is not None

    # 4. Failure after before_write must record possible_partial=True.
    await clear_policy_owner()
    async with AsyncSessionLocal() as session:
        session.add(Target(value="198.51.100.8/32", destination_type="cidr"))
        await session.commit()

    def sync_fail_after_write(
        self: Any,
        snapshot: Any,
        meta: Any,
        *,
        before_write: Any = None,
        **kwargs: Any,
    ) -> None:
        if before_write is not None:
            before_write()
        raise RuntimeError("mid-write error")

    with (
        patch.object(StopligaAdapter, "get_current_routes", AsyncMock(return_value=set())),
        patch("stopliga.routers.opnsense.OPNsenseRouterDriver.sync", sync_fail_after_write),
    ):
        mid_fail = await ReconciliationEngine().reconcile()
    assert not mid_fail.success
    owner = await read_policy_owner()
    assert owner is not None
    assert owner.possible_partial is True


async def test_omada_controller_id_change_retires_previous_policy(
    client: httpx.AsyncClient,
) -> None:
    """Changing omada_controller_id changes policy identity and retires the prior controller."""
    _select_mock()
    with patch("app.core.settings_service._trigger_reconciliation_safely", AsyncMock()):
        activated = await client.put(
            "/api/v1/settings",
            json={
                "router_driver": "omada",
                "omada_base_url": "https://omada-cloud.example.net",
                "omada_client_id": "cid",
                "omada_client_secret": "csec",
                "omada_controller_id": "ctrl-1",
                "omada_target": "vpn-1",
            },
        )
    assert activated.status_code == 200
    await record_policy_owner(settings)

    with (
        patch.object(StopligaAdapter, "teardown", AsyncMock()) as teardown,
        patch("app.core.settings_service._trigger_reconciliation_safely", AsyncMock()),
    ):
        switched = await client.put(
            "/api/v1/settings",
            json={"omada_controller_id": "ctrl-2"},
        )

    assert switched.status_code == 200
    teardown.assert_awaited_once()
    assert settings.omada_controller_id == "ctrl-2"
    assert await read_policy_owner() is None


async def test_cancellation_mid_write_persists_possible_partial_ownership(
    db_ready: None,
) -> None:
    """CancelledError after policy_write_started must persist possible_partial=True."""
    _select_mock()
    await save_settings(
        {
            "router_driver": "unifi",
            "unifi_controller_url": UNIFI_URL,
            "unifi_api_key": API_KEY,
        }
    )
    async with AsyncSessionLocal() as session:
        session.add(Target(value="198.51.100.7/32", destination_type="cidr"))
        await session.commit()

    async def cancelled_apply(
        self: UniFiRouterDriver, targets: set[TargetCIDR], enabled: bool
    ) -> SyncResult:
        self.policy_write_started = True
        raise asyncio.CancelledError

    with (
        patch.object(UniFiRouterDriver, "apply_policy", cancelled_apply),
        pytest.raises(asyncio.CancelledError),
    ):
        await ReconciliationEngine().reconcile()

    owner = await read_policy_owner()
    assert owner is not None
    assert owner.possible_partial is True


async def test_virtual_gateway_write_boundary_handles_absent_stale_and_mid_write_socket(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """VirtualGateway flags write_started only after connecting to a listening socket."""
    _select_mock()
    missing_sock = tmp_path / "missing.sock"
    settings.gateway_control_socket = str(missing_sock)
    async with AsyncSessionLocal() as session:
        session.add(Target(value="198.51.100.7/32", destination_type="cidr"))
        await session.commit()

    with patch("app.core.settings_service._trigger_reconciliation_safely", AsyncMock()):
        activated = await client.put("/api/v1/settings", json={"router_driver": "virtual_gateway"})
    assert activated.status_code == 200

    # 1. Absent socket: no write started, recoverable back to mock.
    res_absent = await ReconciliationEngine().reconcile()
    assert not res_absent.success
    assert await read_policy_owner() is None

    # 2. Stale/dead socket file on disk: open_unix_connection fails before write boundary.
    stale_sock = tmp_path / "stale.sock"
    raw_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    raw_sock.bind(str(stale_sock))
    raw_sock.close()
    settings.gateway_control_socket = str(stale_sock)
    invalidate_driver()

    res_stale = await ReconciliationEngine().reconcile()
    assert not res_stale.success
    assert await read_policy_owner() is None

    with patch("app.core.settings_service._trigger_reconciliation_safely", AsyncMock()):
        back = await client.put("/api/v1/settings", json={"router_driver": "mock"})
    assert back.status_code == 200

    # 3. Live socket that accepts a policy write and disconnects mid-response: possible_partial=True.
    live_sock = tmp_path / "live.sock"
    settings.gateway_control_socket = str(live_sock)
    with patch("app.core.settings_service._trigger_reconciliation_safely", AsyncMock()):
        reactivated = await client.put(
            "/api/v1/settings", json={"router_driver": "virtual_gateway"}
        )
    assert reactivated.status_code == 200

    async def drop_after_read(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readline()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(drop_after_read, path=str(live_sock))
    try:
        driver = VirtualGatewayDriver(socket_path=str(live_sock))
        with patch("app.core.engine.get_driver", return_value=driver):
            res_live = await ReconciliationEngine().reconcile()
        assert not res_live.success
        assert driver.policy_write_started is True
        owner = await read_policy_owner()
        assert owner is not None
        assert owner.possible_partial is True
    finally:
        server.close()
        await server.wait_closed()


async def test_mikrotik_partial_write_vs_missing_fib_ownership(db_ready: None) -> None:
    """MikroTik records partial ownership only after crossing the write boundary."""
    _select_mock()
    async with AsyncSessionLocal() as session:
        session.add(Target(value="198.51.100.7/32", destination_type="cidr"))
        await session.commit()

    await save_settings(
        {
            "router_driver": "mikrotik",
            "mikrotik_api_url": "https://mt.example.net",
            "mikrotik_username": "admin",
            "mikrotik_password": "secret",
            "mikrotik_routing_table": "vpn_table",
        }
    )

    def missing_fib_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    with patch.object(
        MikroTikRouterDriver,
        "_client",
        lambda self: httpx.AsyncClient(
            base_url="https://mt.example.net/rest/",
            transport=httpx.MockTransport(missing_fib_handler),
        ),
    ):
        res_pre = await ReconciliationEngine().reconcile()
    assert not res_pre.success
    assert await read_policy_owner() is None

    def partial_write_handler(request: httpx.Request) -> httpx.Response:
        if "routing/table" in str(request.url):
            return httpx.Response(200, json=[{"name": "vpn_table", "fib": "true"}])
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(500, text="write failed mid-loop")

    with patch.object(
        MikroTikRouterDriver,
        "_client",
        lambda self: httpx.AsyncClient(
            base_url="https://mt.example.net/rest/",
            transport=httpx.MockTransport(partial_write_handler),
        ),
    ):
        res_mid = await ReconciliationEngine().reconcile()
    assert not res_mid.success
    owner = await read_policy_owner()
    assert owner is not None
    assert owner.possible_partial is True


async def test_restart_preserves_ownership_and_blocks_unretired_switch(
    client: httpx.AsyncClient,
) -> None:
    """A simulated process restart keeps router ownership and enforces teardown."""
    _select_mock()
    async with AsyncSessionLocal() as session:
        session.add(Target(value="198.51.100.7/32", destination_type="cidr"))
        await session.commit()

    with patch("app.core.settings_service._trigger_reconciliation_safely", AsyncMock()):
        activated = await client.put(
            "/api/v1/settings",
            json={
                "router_driver": "unifi",
                "unifi_controller_url": UNIFI_URL,
                "unifi_api_key": API_KEY,
                "unifi_vpn_network_id": "vpn-example",
            },
        )
    assert activated.status_code == 200

    route: dict[str, Any] = {
        "_id": "route-example",
        "description": "TeByPass",
        "enabled": True,
        "network_id": "vpn-example",
        "ip_addresses": [{"ip_or_subnet": "198.51.100.7/32"}],
        "target_devices": [{"type": "ALL_CLIENTS"}],
    }

    def ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[route])

    with patch.object(
        UniFiRouterDriver,
        "_client",
        lambda self: httpx.AsyncClient(transport=httpx.MockTransport(ok_handler)),
    ):
        applied = await ReconciliationEngine().reconcile()
    assert applied.success

    _select_mock()
    invalidate_driver()
    await load_into_settings()
    assert settings.router_driver == "unifi"
    assert settings.unifi_controller_url == UNIFI_URL

    with patch.object(
        UniFiRouterDriver, "teardown", AsyncMock(side_effect=RuntimeError("offline"))
    ):
        blocked = await client.put("/api/v1/settings", json={"router_driver": "mock"})
    assert blocked.status_code == 502
    assert settings.router_driver == "unifi"


async def test_disabled_unifi_policy_keeps_ownership_across_repeated_empty_cycles(
    db_ready: None,
) -> None:
    """UniFi retains a disabled route across repeated empty cycles without losing ownership."""
    _select_mock()
    await save_settings(
        {
            "router_driver": "unifi",
            "unifi_controller_url": UNIFI_URL,
            "unifi_api_key": API_KEY,
            "unifi_vpn_network_id": "vpn-example",
        }
    )
    route: dict[str, Any] = {
        "_id": "route-example",
        "description": "TeByPass",
        "enabled": True,
        "network_id": "vpn-example",
        "ip_addresses": [{"ip_or_subnet": "198.51.100.7/32"}],
        "target_devices": [{"type": "ALL_CLIENTS"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            route.update(json.loads(request.content))
        return httpx.Response(200, json=[route])

    engine = ReconciliationEngine()
    with patch.object(
        UniFiRouterDriver,
        "_client",
        lambda self: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ):
        first = await engine.reconcile()
        second = await engine.reconcile()

    assert first.success and second.success
    assert route["enabled"] is False
    assert await read_policy_owner() is not None
