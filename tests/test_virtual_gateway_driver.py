"""Tests for the VirtualGatewayDriver Unix socket transport and fallbacks."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import Any

import pytest

from app.drivers.base import DriverHealth, TargetCIDR
from app.drivers.virtual_gateway import VirtualGatewayDriver

Handler = Callable[[dict[str, Any]], dict[str, Any]]


class FakeGateway:
    """Minimal stand-in for the sidecar control daemon."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.requests: list[dict[str, Any]] = []
        self._server: asyncio.AbstractServer | None = None
        self.handler: Handler = lambda payload: {"ok": False, "error": "no handler"}

    async def start(self) -> None:
        self._server = await asyncio.start_unix_server(self._serve, path=str(self.path))

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = await reader.readline()
        payload = json.loads(line)
        self.requests.append(payload)
        writer.write(json.dumps(self.handler(payload)).encode() + b"\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()


@pytest.fixture
async def gateway_socket(tmp_path: Path) -> AsyncGenerator[FakeGateway, None]:
    fake = FakeGateway(tmp_path / "gateway.sock")
    await fake.start()
    try:
        yield fake
    finally:
        await fake.stop()


@pytest.fixture
def driver(gateway_socket: FakeGateway) -> VirtualGatewayDriver:
    return VirtualGatewayDriver(
        set_name="tebypass_targets",
        socket_path=str(gateway_socket.path),
    )


async def test_get_current_routes_uses_socket(
    driver: VirtualGatewayDriver, gateway_socket: FakeGateway
) -> None:
    gateway_socket.handler = lambda payload: {
        "ok": True,
        "elements": ["198.51.100.0/24", "203.0.113.7"],
        "total": 2,
    }
    routes = await driver.get_current_routes()
    assert routes == {"198.51.100.0/24", "203.0.113.7/32"}
    assert gateway_socket.requests == [{"cmd": "list"}]


async def test_host_elements_are_canonicalised(
    driver: VirtualGatewayDriver, gateway_socket: FakeGateway
) -> None:
    """The sidecar reports host addresses without /32; the driver must not churn."""
    gateway_socket.handler = lambda payload: {"ok": True, "elements": ["198.51.100.7"], "total": 1}
    assert await driver.get_current_routes() == {"198.51.100.7/32"}


async def test_sync_routes_sends_delta_and_honours_response(
    driver: VirtualGatewayDriver, gateway_socket: FakeGateway
) -> None:
    gateway_socket.handler = lambda payload: {
        "ok": True,
        "added_count": 1,
        "removed_count": 1,
        "total_active": 1,
        "duration_ms": 4.5,
        "elements": ["198.51.100.0/24"],
    }
    result = await driver.sync_routes(
        to_add={TargetCIDR(cidr="198.51.100.0/24", target_id="t-1")},
        to_remove={"203.0.113.0/24"},
    )
    assert result.success is True
    assert (result.added_count, result.removed_count, result.total_active) == (1, 1, 1)
    assert result.duration_ms == 4.5
    assert gateway_socket.requests[0]["cmd"] == "sync"
    assert gateway_socket.requests[0]["add"] == ["198.51.100.0/24"]
    assert gateway_socket.requests[0]["remove"] == ["203.0.113.0/24"]
    assert await driver.get_current_routes() == {"198.51.100.0/24"}


async def test_sync_routes_reports_gateway_error(
    driver: VirtualGatewayDriver, gateway_socket: FakeGateway
) -> None:
    gateway_socket.handler = lambda payload: {"ok": False, "error": "nft transaction failed"}
    result = await driver.sync_routes(
        to_add={TargetCIDR(cidr="198.51.100.0/24", target_id="t-1")},
        to_remove=set(),
    )
    assert result.success is False
    assert result.added_count == 0
    assert result.error_message == "nft transaction failed"


async def test_health_check_socket_healthy(
    driver: VirtualGatewayDriver, gateway_socket: FakeGateway
) -> None:
    gateway_socket.handler = lambda payload: {
        "ok": True,
        "interface": "wg0",
        "wireguard": {"present": True, "handshake_age": 42},
        "nftables": {
            "table": True,
            "set": True,
            "set_name": "tebypass_targets",
            "elements_count": 2,
            "mark_rule": True,
        },
        "elements": ["198.51.100.0/24", "203.0.113.7"],
    }
    status = await driver.health_check()
    assert status.health == DriverHealth.HEALTHY
    assert status.active_routes_count == 2
    assert status.details["transport"] == "unix_socket"
    assert status.details["wireguard_handshake_age"] == 42


async def test_health_check_socket_degraded_without_tunnel(
    driver: VirtualGatewayDriver, gateway_socket: FakeGateway
) -> None:
    gateway_socket.handler = lambda payload: {
        "ok": False,
        "interface": "wg0",
        "wireguard": {"present": False, "handshake_age": None},
        "nftables": {
            "table": True,
            "set": True,
            "set_name": "tebypass_targets",
            "elements_count": 0,
            "mark_rule": True,
        },
        "elements": [],
    }
    status = await driver.health_check()
    assert status.health == DriverHealth.DEGRADED
    assert status.details["wireguard_up"] is False


async def test_check_health_alias_is_equivalent(
    driver: VirtualGatewayDriver, gateway_socket: FakeGateway
) -> None:
    gateway_socket.handler = lambda payload: {
        "ok": True,
        "wireguard": {"present": True, "handshake_age": 1},
        "nftables": {"table": True, "set": True, "elements_count": 0, "mark_rule": True},
        "elements": [],
    }
    direct = await driver.health_check()
    alias = await driver.check_health()
    assert direct.health == alias.health == DriverHealth.HEALTHY


async def test_fallback_without_socket_is_emulated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = VirtualGatewayDriver(socket_path=str(tmp_path / "missing.sock"))
    monkeypatch.setattr(driver, "_nft_binary", lambda: None)

    result = await driver.sync_routes(
        to_add={TargetCIDR(cidr="198.51.100.0/24", target_id="t-1")},
        to_remove=set(),
    )
    assert result.success is True
    assert result.added_count == 1
    assert await driver.get_current_routes() == {"198.51.100.0/24"}

    status = await driver.health_check()
    assert status.health == DriverHealth.DEGRADED
    assert status.details["transport"] == "emulated"
    assert status.active_routes_count == 1


async def test_unreachable_socket_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A socket file left behind by a dead daemon must not break the driver."""
    dead = tmp_path / "dead.sock"
    dead.write_text("")
    driver = VirtualGatewayDriver(socket_path=str(dead))
    monkeypatch.setattr(driver, "_nft_binary", lambda: None)

    result = await driver.sync_routes(
        to_add={TargetCIDR(cidr="203.0.113.0/24", target_id="t-2")},
        to_remove=set(),
    )
    assert result.success is True
    assert result.added_count == 1


def test_canonical_cidr_edge_cases() -> None:
    from app.drivers.virtual_gateway import canonical_cidr

    assert canonical_cidr("198.51.100.7") == "198.51.100.7/32"
    assert canonical_cidr("198.51.100.0/24") == "198.51.100.0/24"
    assert canonical_cidr("  ") == ""
    assert canonical_cidr("not-an-ip") == "not-an-ip"


async def test_close_is_idempotent(driver: VirtualGatewayDriver) -> None:
    await driver.close()
    await driver.close()
