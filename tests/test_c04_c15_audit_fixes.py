"""Verification tests for audit findings C04 through C15."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select, text

from app.core.cache import SlidingWindowCache
from app.core.community import InvalidISPFilterError, parse_snapshot
from app.core.config import Settings, settings
from app.core.resolver import NativeDNSResolver, classify_dns_exception
from app.db.models import Feed, Target
from app.db.session import AsyncSessionLocal, _migrate_manual_targets_uniqueness, engine
from app.drivers.base import DriverHealth
from app.drivers.mikrotik import MikroTikRouterDriver
from app.drivers.stopliga_adapter import StopligaAdapter
from app.drivers.unifi import UniFiRouterDriver
from app.drivers.virtual_gateway import VirtualGatewayDriver


def _community_payload(blocked: bool = True) -> dict[str, Any]:
    now = datetime.now(UTC)
    recent = (now - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    return {
        "lastUpdate": now.isoformat().replace("+00:00", "Z"),
        "blocked": blocked,
        "data": [
            {
                "ip": "198.51.100.7",
                "isp": "ISP_ALPHA",
                "stateChanges": [{"timestamp": recent, "state": True}],
            },
            {
                "ip": "203.0.113.8",
                "isp": "ISP_BETA",
                "stateChanges": [{"timestamp": recent, "state": True}],
            },
        ],
    }


# ---------------------------------------------------------------------------
# C04: DNS visibility, error classification & uncovered targets degradation
# ---------------------------------------------------------------------------


def test_classify_dns_exception_maps_nxdomain_timeout_and_budget() -> None:
    import socket

    assert (
        classify_dns_exception(socket.gaierror(8, "nodename nor servname provided"))[0]
        == "nxdomain"
    )
    assert classify_dns_exception(TimeoutError("timed out"))[0] == "timeout"


async def test_c04_uncovered_fqdn_degrades_status_when_routing_enabled(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.core.routes_service.NativeDNSResolver.resolve_fqdn_with_status",
        AsyncMock(side_effect=TimeoutError("DNS lookup timed out")),
    )
    resp = await client.post("/api/v1/targets", json={"value": "unreachable.example.invalid"})
    assert resp.status_code == 201
    target_data = resp.json()
    assert target_data["dns_status"] == "timeout"
    assert target_data["dns_error"]

    status_resp = await client.get("/api/v1/status")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["dns_errors"] >= 1
    assert status_data["uncovered_targets"] >= 1
    assert status_data["status"] == "degraded"


async def test_c04_resolver_records_empty_and_nxdomain_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket

    resolver = NativeDNSResolver(cache=SlidingWindowCache())

    async def empty_getaddrinfo(*args: Any, **kwargs: Any) -> list[Any]:
        return []

    loop = __import__("asyncio").get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", empty_getaddrinfo)
    empty_res = await resolver.resolve_fqdn_with_status("empty.example.org")
    assert empty_res.status == "empty"
    assert empty_res.resolutions == []

    async def nxdomain_getaddrinfo(*args: Any, **kwargs: Any) -> list[Any]:
        raise socket.gaierror(8, "Name or service not known")

    monkeypatch.setattr(loop, "getaddrinfo", nxdomain_getaddrinfo)
    nxd_res = await resolver.resolve_fqdn_with_status("missing.example.invalid")
    assert nxd_res.status == "nxdomain"
    assert nxd_res.error is not None


# ---------------------------------------------------------------------------
# C05: UniFi multi-VPN selection, errors & candidate discovery
# ---------------------------------------------------------------------------


async def test_c05_unifi_get_vpn_networks_raises_on_http_error() -> None:
    driver = UniFiRouterDriver(
        Settings(
            router_driver="unifi",
            unifi_controller_url="https://198.51.100.1",
            unifi_api_key="test-key",
        )
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if "/api/self/sites" in str(request.url):
            return httpx.Response(200, json={"data": [{"name": "default"}]})
        return httpx.Response(401, json={"meta": {"rc": "error"}})

    driver._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="HTTP 401"):
        await driver.get_vpn_networks()


async def test_c05_unifi_multi_vpn_requires_explicit_selection() -> None:
    from app.drivers.base import TargetCIDR

    driver = UniFiRouterDriver(
        Settings(
            router_driver="unifi",
            unifi_controller_url="https://198.51.100.1",
            unifi_api_key="test-key",
            unifi_vpn_network_id=None,
        )
    )
    vpns = [
        {"_id": "vpn-1", "name": "WG-One", "purpose": "vpn-client", "vpn_type": "wireguard-client"},
        {"_id": "vpn-2", "name": "WG-Two", "purpose": "vpn-client", "vpn_type": "wireguard-client"},
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/api/self/sites" in url:
            return httpx.Response(200, json={"data": [{"name": "default"}]})
        if "/trafficroutes" in url:
            return httpx.Response(200, json=[])
        if "/rest/networkconf" in url:
            return httpx.Response(200, json={"data": vpns})
        return httpx.Response(404)

    driver._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="Multiple UniFi VPN Client networks"):
        await driver.sync_routes({TargetCIDR("198.51.100.10/32", "t1")}, set(), force=True)


async def test_c05_unifi_post_vpn_networks_uses_candidate_without_persisting(
    client: httpx.AsyncClient,
) -> None:
    captured: dict[str, Any] = {}

    class StubUniFi:
        def __init__(self, cfg: Settings) -> None:
            captured["url"] = cfg.unifi_controller_url
            captured["key"] = cfg.unifi_api_key

        async def get_vpn_networks(self) -> list[dict[str, str]]:
            return [{"id": "vpn-cand", "name": "Candidate-WG", "type": "wireguard-client"}]

        async def close(self) -> None:
            return None

    with patch("app.core.settings_service.UniFiRouterDriver", StubUniFi):
        resp = await client.post(
            "/api/v1/settings/unifi/vpn-networks",
            json={"unifi_controller_url": "https://203.0.113.10", "unifi_api_key": "cand-secret"},
        )
    assert resp.status_code == 200
    assert resp.json() == [{"id": "vpn-cand", "name": "Candidate-WG", "type": "wireguard-client"}]
    assert captured["url"] == "https://203.0.113.10"
    assert settings.unifi_controller_url != "https://203.0.113.10"


# ---------------------------------------------------------------------------
# C06: OPNsense rule binding verification & MikroTik FIB validation
# ---------------------------------------------------------------------------


def test_c06_opnsense_rule_binding_rejects_wrong_direction_or_missing_gateway() -> None:
    adapter = StopligaAdapter(Settings(router_driver="opnsense", opnsense_route_name="TeByPass"))

    class FakeClient:
        def __init__(self, rule_detail: dict[str, Any]) -> None:
            self._detail = rule_detail

        def request(self, method: str, path: str) -> dict[str, Any]:
            return {"rule": self._detail}

    with pytest.raises(RuntimeError, match="dirección 'out'"):
        adapter._verify_opnsense_rule_binding(
            FakeClient(
                {
                    "action": "pass",
                    "direction": "out",
                    "destination": "TeByPass_Targets",
                    "gateway": "WG_GW",
                }
            ),
            {"uuid": "r-1", "enabled": "1"},
            {"uuid": "a-1"},
        )

    with pytest.raises(RuntimeError, match="gateway VPN"):
        adapter._verify_opnsense_rule_binding(
            FakeClient(
                {
                    "action": "pass",
                    "direction": "in",
                    "destination": "TeByPass_Targets",
                    "destination_not": "0",
                    "gateway": "default",
                }
            ),
            {"uuid": "r-2", "enabled": "1"},
            {"uuid": "a-1"},
        )

    fully_verified, warning, manual = adapter._verify_opnsense_rule_binding(
        FakeClient({"description": "TeByPass"}),
        {"uuid": "r-3", "enabled": "1"},
        {"uuid": "a-1"},
    )
    assert fully_verified is False
    assert warning is not None and "Verificación parcial" in warning
    assert manual is not None and "Firewall > Rules" in manual


async def test_c06_mikrotik_health_check_requires_fib_table() -> None:
    driver = MikroTikRouterDriver(
        Settings(
            mikrotik_api_url="https://router.example.net",
            mikrotik_username="admin",
            mikrotik_password="pw",
            mikrotik_routing_table="vpn_fib",
        )
    )

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("routing/table"):
            return httpx.Response(200, json=[{"name": "vpn_fib", "fib": "false"}])
        return httpx.Response(200, json=[])

    driver._client = lambda: httpx.AsyncClient(  # type: ignore[method-assign]
        base_url="https://router.example.net/rest/",
        transport=httpx.MockTransport(handle),
    )
    status = await driver.health_check()
    assert status.health == DriverHealth.UNHEALTHY
    assert status.details["ready"] is False
    assert "Tabla FIB" in str(status.details["error"])


# ---------------------------------------------------------------------------
# C07 & C10: Non-mutating test-router, skip_auto_sync, diagnostic stages
# ---------------------------------------------------------------------------


async def test_c07_test_router_does_not_persist_or_trigger_sync(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_mock = AsyncMock()
    monkeypatch.setattr("app.core.settings_service._trigger_reconciliation_safely", sync_mock)

    initial_driver = settings.router_driver
    resp = await client.post(
        "/api/v1/settings/test-router",
        json={
            "router_driver": "mikrotik",
            "mikrotik_api_url": "https://router.example.net",
            "mikrotik_username": "tester",
            "mikrotik_password": "secret",
            "mikrotik_routing_table": "vpn",
        },
    )
    assert resp.status_code == 200
    assert settings.router_driver == initial_driver
    assert sync_mock.await_count == 0


async def test_c07_put_settings_skip_auto_sync_defers_reconciliation(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_mock = AsyncMock()
    monkeypatch.setattr("app.core.settings_service._trigger_reconciliation_safely", sync_mock)

    resp = await client.put(
        "/api/v1/settings",
        json={"sync_interval_seconds": 60, "skip_auto_sync": True},
    )
    assert resp.status_code == 200
    assert resp.json()["sync_interval_seconds"] == 60
    assert sync_mock.await_count == 0


async def test_c10_virtual_gateway_health_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wg_conf = tmp_path / "wg0.conf"
    monkeypatch.setattr(settings, "wireguard_config_path", str(wg_conf))

    driver = VirtualGatewayDriver(
        set_name="tebypass_targets", socket_path=str(tmp_path / "missing.sock")
    )
    down = await driver.health_check()
    assert down.health == DriverHealth.UNHEALTHY
    assert down.details["stage"] == "socket_unavailable"

    wg_conf.write_text("[Interface]\nPrivateKey = fake\n", encoding="utf-8")
    pending = driver._status_from_socket(
        {
            "ok": False,
            "interface": "wg0",
            "wireguard": {"present": False, "handshake_age": None},
            "nftables": {
                "table": True,
                "set": True,
                "set_name": "tebypass_targets",
                "mark_rule": True,
            },
            "elements": [],
        }
    )
    assert pending.health == DriverHealth.DEGRADED
    assert pending.details["stage"] == "wireguard_down"

    wg_conf.unlink()
    unconfigured = driver._status_from_socket(
        {
            "ok": False,
            "interface": "wg0",
            "wireguard": {"present": False, "handshake_age": None},
            "nftables": {
                "table": True,
                "set": True,
                "set_name": "tebypass_targets",
                "mark_rule": True,
            },
            "elements": [],
        }
    )
    assert unconfigured.health == DriverHealth.DEGRADED
    assert unconfigured.details["stage"] == "pending_wireguard"


# ---------------------------------------------------------------------------
# C11: Gateway controller rejects custom TEBYPASS_GATEWAY_WG_IFACE
# ---------------------------------------------------------------------------


def test_c11_gateway_controller_rejects_non_wg0_interface(monkeypatch: pytest.MonkeyPatch) -> None:
    controller_path = Path(__file__).resolve().parents[1] / "gateway" / "controller.py"
    spec = importlib.util.spec_from_file_location("tebypass_gw_ctrl_c11", controller_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setenv("TEBYPASS_GATEWAY_WG_IFACE", "wg0")
    assert mod.validate_wg_interface_env() == "wg0"

    monkeypatch.setenv("TEBYPASS_GATEWAY_WG_IFACE", "wg1")
    with pytest.raises(mod.GatewayError, match="wg0"):
        mod.validate_wg_interface_env()


# ---------------------------------------------------------------------------
# C12: /api/v1/routes/active returns sanitized 502 on driver error
# ---------------------------------------------------------------------------


async def test_c12_routes_active_returns_502_sanitized_on_driver_error(
    client: httpx.AsyncClient,
) -> None:
    class BrokenDriver:
        driver_name = "mikrotik"

        async def get_current_routes(self, force: bool = False) -> set[str]:
            raise RuntimeError("api_key=leak-me-secret\nstacktrace-line")

    with patch("app.core.routes_service.get_driver", return_value=BrokenDriver()):
        resp = await client.get("/api/v1/routes/active")
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert "leak-me-secret" not in detail
    assert "stacktrace-line" not in detail
    assert "<redacted>" in detail


# ---------------------------------------------------------------------------
# C13 & C14: Manual target uniqueness across folders & bulk invalid feedback
# ---------------------------------------------------------------------------


async def test_c13_and_c14_manual_target_uniqueness_and_bulk_invalid_lines(
    client: httpx.AsyncClient,
) -> None:
    f1 = (await client.post("/api/v1/folders", json={"name": "FolderA"})).json()
    f2 = (await client.post("/api/v1/folders", json={"name": "FolderB"})).json()

    created = await client.post(
        "/api/v1/targets",
        json={"value": "198.51.100.44", "folder_id": f1["id"]},
    )
    assert created.status_code == 201
    target_id = created.json()["id"]

    # Duplicate across different folder -> 409 Conflict
    dup = await client.post(
        "/api/v1/targets",
        json={"value": "198.51.100.44/32", "folder_id": f2["id"]},
    )
    assert dup.status_code == 409

    # Create a second valid target and try PATCHing it to collide -> 409 Conflict
    other = (
        await client.post("/api/v1/targets", json={"value": "203.0.113.55", "folder_id": f2["id"]})
    ).json()
    collision_patch = await client.patch(
        f"/api/v1/targets/{other['id']}",
        json={"value": "198.51.100.44/32"},
    )
    assert collision_patch.status_code == 409

    # Bulk import: 1 valid, 1 duplicate skipped, 1 invalid rejected with line details
    bulk = await client.post(
        "/api/v1/targets/bulk",
        json={
            "entries": ["203.0.113.66", "198.51.100.44", "not a valid cidr or fqdn !!!"],
            "folder_id": f2["id"],
        },
    )
    assert bulk.status_code == 200
    payload = bulk.json()
    assert payload["created"] == 1
    assert payload["skipped"] == 1
    assert payload["invalid"] == ["not a valid cidr or fqdn !!!"]

    # Cleanup check
    del_resp = await client.delete(f"/api/v1/targets/{target_id}")
    assert del_resp.status_code == 204


async def test_c13_migration_dedupes_manual_targets_across_folders(db_ready: None) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("DROP INDEX IF EXISTS uq_targets_manual_value"))
        await conn.execute(
            text(
                "INSERT INTO targets (id, feed_id, folder_id, value, destination_type, enabled, created_at) "
                "VALUES ('m1', NULL, NULL, '198.51.100.77/32', 'cidr', 1, CURRENT_TIMESTAMP), "
                "('m2', NULL, NULL, '198.51.100.77', 'cidr', 0, CURRENT_TIMESTAMP)"
            )
        )
        await _migrate_manual_targets_uniqueness(conn)

    async with AsyncSessionLocal() as db:
        remaining = list((await db.execute(select(Target))).scalars().all())
        assert len(remaining) == 1
        assert remaining[0].value == "198.51.100.77/32"


# ---------------------------------------------------------------------------
# C15: Invalid feed ISP filter persists actionable error & invalidates ETag
# ---------------------------------------------------------------------------


def test_c15_parse_snapshot_invalid_isp_lists_available_options() -> None:
    with pytest.raises(InvalidISPFilterError) as exc_info:
        parse_snapshot(_community_payload(True), isp="NONEXISTENT_ISP")
    msg = str(exc_info.value)
    assert "ISP_ALPHA" in msg
    assert "ISP_BETA" in msg


async def test_c15_invalid_isp_preserves_previous_targets_and_sets_last_error(
    db_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.feeds_sync import refresh_feed

    async with AsyncSessionLocal() as db:
        feed = Feed(
            name="Comunidad",
            url="https://feed.example.org/status.json",
            format="hayahora",
            enabled=True,
            etag='"etag-1"',
            blocked=True,
        )
        db.add(feed)
        await db.flush()
        db.add(
            Target(
                feed_id=feed.id,
                value="198.51.100.7/32",
                destination_type="cidr",
                enabled=True,
            )
        )
        await db.commit()
        feed_id = feed.id

    monkeypatch.setattr(settings, "feed_isp_filter", "WRONG_ISP")
    monkeypatch.setattr(
        "app.core.feeds_sync.download",
        AsyncMock(return_value=(json.dumps(_community_payload(True)), '"etag-2"')),
    )

    async with AsyncSessionLocal() as db:
        loaded_feed = await db.get(Feed, feed_id)
        assert loaded_feed is not None
        imported = await refresh_feed(db, loaded_feed, force=True)
        assert imported == 0

    async with AsyncSessionLocal() as db:
        refreshed = await db.get(Feed, feed_id)
        assert refreshed is not None
        assert refreshed.last_error is not None
        assert "ISP_ALPHA" in refreshed.last_error
        assert refreshed.etag == '"etag-1"'
        targets = list(
            (await db.execute(select(Target).where(Target.feed_id == feed_id))).scalars().all()
        )
        assert [t.value for t in targets] == ["198.51.100.7/32"]


async def test_c15_changing_isp_filter_invalidates_community_feed_etag(
    client: httpx.AsyncClient,
) -> None:
    async with AsyncSessionLocal() as db:
        feed = Feed(
            name="Comunidad",
            url="https://feed.example.org/status.json",
            format="hayahora",
            enabled=True,
            etag='"etag-cached"',
            last_fetched_at=datetime.now(UTC),
        )
        db.add(feed)
        await db.commit()
        feed_id = feed.id

    resp = await client.put(
        "/api/v1/settings",
        json={"feed_isp_filter": "ISP_ALPHA", "skip_auto_sync": True},
    )
    assert resp.status_code == 200

    async with AsyncSessionLocal() as db:
        refreshed = await db.get(Feed, feed_id)
        assert refreshed is not None
        assert refreshed.etag is None
        assert refreshed.last_fetched_at is None


# ---------------------------------------------------------------------------
# UI sanity check: verify HTML contains accessibility & audit UX markers
# ---------------------------------------------------------------------------


async def test_ui_template_contains_audit_ux_and_a11y_hooks(client: httpx.AsyncClient) -> None:
    resp = await client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert 'id="form-live-announcer"' in html
    assert ":user-invalid" in html
    assert ".visually-hidden" in html
    assert "/api/v1/settings/unifi/vpn-networks" in html
    assert "skip_auto_sync" in html
    assert "uncovered_targets" in html
    assert "feed_errors" in html
