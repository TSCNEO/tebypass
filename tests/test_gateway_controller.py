"""Unit and integration tests for the gateway control daemon (gateway/controller.py)."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
from collections.abc import AsyncGenerator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

CONTROLLER_PATH = Path(__file__).resolve().parents[1] / "gateway" / "controller.py"

_ADD_DELETE = re.compile(
    r"^(add|delete) element inet tebypass tebypass_targets \{ (.*) \}$",
)


def _load_controller() -> ModuleType:
    spec = importlib.util.spec_from_file_location("tebypass_gateway_controller", CONTROLLER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


controller = _load_controller()


def _execute(script: str, state: set[str]) -> None:
    """Tiny interpreter for the exact nftables fragments the controller emits."""
    for raw_line in script.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _ADD_DELETE.match(line)
        if match:
            items = {item.strip() for item in match.group(2).split(",") if item.strip()}
            if match.group(1) == "add":
                state.update(items)
            else:
                state.difference_update(items)
        elif line.startswith("flush set"):
            state.clear()
        else:  # pragma: no cover - guards against emitting unrecognised syntax
            raise AssertionError(f"unexpected nftables statement: {line}")


@pytest.fixture
def nft_state(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Replace the nftables layer with an in-memory element set."""
    state: set[str] = {"203.0.113.0/24"}

    async def fake_list() -> list[str]:
        return sorted(state)

    async def fake_apply(script: str) -> None:
        _execute(script, state)

    monkeypatch.setattr(controller, "list_elements", fake_list)
    monkeypatch.setattr(controller, "apply_ruleset", fake_apply)
    return state


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("198.51.100.5", "198.51.100.5/32"),
        ("198.51.100.5/32", "198.51.100.5/32"),
        ("198.51.100.5/24", "198.51.100.0/24"),
        ("  203.0.113.0/25  ", "203.0.113.0/25"),
    ],
)
def test_normalize_cidr_canonicalises(raw: str, expected: str) -> None:
    assert controller.normalize_cidr(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not-an-ip",
        "198.51.100.5; flush set inet tebypass tebypass_targets",
        "198.51.100.0/33",
        "2001:db8::/32",
    ],
)
def test_normalize_cidr_rejects_invalid_input(raw: str) -> None:
    with pytest.raises(controller.GatewayError):
        controller.normalize_cidr(raw)


def test_normalize_cidr_rejects_non_string() -> None:
    with pytest.raises(controller.GatewayError):
        controller.normalize_cidr(123)


def test_parse_set_elements_reads_nft_json() -> None:
    payload = json.dumps(
        {
            "nftables": [
                {"metainfo": {"version": "1.0.9"}},
                {
                    "set": {
                        "family": "inet",
                        "name": "tebypass_targets",
                        "elem": [{"prefix": {"addr": "198.51.100.0", "len": 24}}, "203.0.113.7"],
                    }
                },
            ]
        }
    )
    assert controller.parse_set_elements(payload) == ["198.51.100.0/24", "203.0.113.7"]


def test_parse_set_elements_rejects_garbage() -> None:
    with pytest.raises(controller.GatewayError):
        controller.parse_set_elements("{not json")


def test_canonical_elements_restores_the_prefix() -> None:
    assert controller.canonical_elements(["203.0.113.7", "198.51.100.0/24"]) == [
        "198.51.100.0/24",
        "203.0.113.7/32",
    ]


async def test_host_addresses_are_stable_across_syncs(nft_state: set[str]) -> None:
    """nft drops the /32 of host elements; the diff must not churn because of it."""
    nft_state.clear()
    nft_state.add("203.0.113.7")

    listing = await controller.dispatch({"cmd": "list"})
    assert listing["elements"] == ["203.0.113.7/32"]

    synced = await controller.dispatch({"cmd": "sync", "add": ["203.0.113.7/32"], "remove": []})
    assert synced["ok"] is True
    assert synced["added_count"] == 0
    assert synced["total_active"] == 1


def test_scripts_use_the_expected_syntax() -> None:
    delta = controller.build_delta_script(["198.51.100.0/24"], ["203.0.113.0/24"])
    assert delta == (
        "delete element inet tebypass tebypass_targets { 203.0.113.0/24 }\n"
        "add element inet tebypass tebypass_targets { 198.51.100.0/24 }\n"
    )
    assert controller.build_delta_script([], []) == ""
    replace = controller.build_replace_script(["198.51.100.0/24"])
    assert replace == (
        "flush set inet tebypass tebypass_targets\n"
        "add element inet tebypass tebypass_targets { 198.51.100.0/24 }\n"
    )


# --------------------------------------------------------------------------- #
# Command handlers
# --------------------------------------------------------------------------- #


async def test_sync_adds_and_removes(nft_state: set[str]) -> None:
    response = await controller.dispatch(
        {
            "cmd": "sync",
            "add": ["198.51.100.0/24", "198.51.100.0/24"],
            "remove": ["203.0.113.0/24", "192.0.2.0/24"],
        }
    )
    assert response["ok"] is True
    assert response["added_count"] == 1
    assert response["removed_count"] == 1
    assert response["total_active"] == 1
    assert nft_state == {"198.51.100.0/24"}


async def test_sync_without_changes_skips_nftables(
    nft_state: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def spy(script: str) -> None:
        calls.append(script)

    monkeypatch.setattr(controller, "apply_ruleset", spy)
    response = await controller.dispatch({"cmd": "sync", "add": ["203.0.113.0/24"], "remove": []})
    assert response["ok"] is True
    assert response["added_count"] == 0
    assert calls == []


async def test_sync_falls_back_to_full_replace(
    nft_state: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts: list[str] = []
    real_apply = controller.apply_ruleset

    async def flaky_apply(script: str) -> None:
        scripts.append(script)
        if "delete element" in script:
            raise controller.GatewayError("nft transaction failed: interval overlaps")
        await real_apply(script)

    monkeypatch.setattr(controller, "apply_ruleset", flaky_apply)
    response = await controller.dispatch(
        {"cmd": "sync", "add": ["198.51.100.0/24"], "remove": ["203.0.113.0/24"]}
    )
    assert response["ok"] is True
    assert any(script.startswith("flush set") for script in scripts)
    assert nft_state == {"198.51.100.0/24"}


async def test_sync_reports_bad_payload(nft_state: set[str]) -> None:
    response = await controller.dispatch({"cmd": "sync", "add": "198.51.100.0/24", "remove": []})
    assert response["ok"] is False
    assert "list" in str(response["error"])


async def test_flush_and_list(nft_state: set[str]) -> None:
    listing = await controller.dispatch({"cmd": "list"})
    assert listing == {"ok": True, "elements": ["203.0.113.0/24"], "total": 1}

    flushed = await controller.dispatch({"cmd": "flush"})
    assert flushed["ok"] is True
    assert nft_state == set()


async def test_status_reports_partial_state(
    monkeypatch: pytest.MonkeyPatch, nft_state: set[str]
) -> None:
    async def wg_up() -> dict[str, Any]:
        return {"present": True, "handshake_age": 12}

    async def rule_ok() -> bool:
        return True

    async def nft_ok() -> dict[str, Any]:
        return {"table": True, "set": True, "elements": sorted(nft_state)}

    monkeypatch.setattr(controller, "wireguard_state", wg_up)
    monkeypatch.setattr(controller, "mark_rule_present", rule_ok)
    monkeypatch.setattr(controller, "nftables_state", nft_ok)

    status = await controller.dispatch({"cmd": "status"})
    assert status["ok"] is True
    assert status["wireguard"]["handshake_age"] == 12
    assert status["nftables"]["elements_count"] == 1
    assert status["elements"] == ["203.0.113.0/24"]


async def test_status_is_not_ok_without_wireguard(monkeypatch: pytest.MonkeyPatch) -> None:
    async def wg_down() -> dict[str, Any]:
        return {"present": False, "handshake_age": None}

    async def rule_ok() -> bool:
        return True

    async def nft_ok() -> dict[str, Any]:
        return {"table": True, "set": True, "elements": []}

    monkeypatch.setattr(controller, "wireguard_state", wg_down)
    monkeypatch.setattr(controller, "mark_rule_present", rule_ok)
    monkeypatch.setattr(controller, "nftables_state", nft_ok)

    status = await controller.dispatch({"cmd": "status"})
    assert status["ok"] is False
    assert status["nftables"]["set"] is True


async def test_unknown_command_and_ping() -> None:
    assert (await controller.dispatch({"cmd": "ping"}))["pong"] is True
    unknown = await controller.dispatch({"cmd": "reload"})
    assert unknown["ok"] is False
    assert "unknown command" in str(unknown["error"])


# --------------------------------------------------------------------------- #
# Socket server round trip
# --------------------------------------------------------------------------- #


@pytest.fixture
async def control_server(tmp_path: Path) -> AsyncGenerator[str, None]:
    socket_path = str(tmp_path / "gateway.sock")
    server = controller.ControlServer(socket_path)
    await server.start()
    try:
        yield socket_path
    finally:
        await server.close()


async def _call(socket_path: str, payload: dict[str, Any]) -> dict[str, Any]:
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        writer.write(json.dumps(payload).encode() + b"\n")
        await writer.drain()
        line = await reader.readline()
    finally:
        writer.close()
        await writer.wait_closed()
    decoded = json.loads(line)
    assert isinstance(decoded, dict)
    return decoded


async def test_socket_permissions_and_round_trip(
    control_server: str, nft_state: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    mode = Path(control_server).stat().st_mode & 0o777
    assert mode == 0o600

    async def wg_up() -> dict[str, Any]:
        return {"present": True, "handshake_age": 3}

    async def rule_ok() -> bool:
        return True

    async def nft_ok() -> dict[str, Any]:
        return {"table": True, "set": True, "elements": sorted(nft_state)}

    monkeypatch.setattr(controller, "wireguard_state", wg_up)
    monkeypatch.setattr(controller, "mark_rule_present", rule_ok)
    monkeypatch.setattr(controller, "nftables_state", nft_ok)

    status = await _call(control_server, {"cmd": "status"})
    assert status["ok"] is True

    synced = await _call(
        control_server,
        {"cmd": "sync", "add": ["198.51.100.0/24"], "remove": ["203.0.113.0/24"]},
    )
    assert synced["ok"] is True
    assert synced["added_count"] == 1
    assert synced["removed_count"] == 1

    listed = await _call(control_server, {"cmd": "list"})
    assert listed["elements"] == ["198.51.100.0/24"]


async def test_socket_rejects_invalid_json(control_server: str) -> None:
    reader, writer = await asyncio.open_unix_connection(control_server)
    try:
        writer.write(b"this is not json\n")
        await writer.drain()
        line = await reader.readline()
    finally:
        writer.close()
        await writer.wait_closed()
    response = json.loads(line)
    assert response["ok"] is False
    assert "invalid JSON" in response["error"]
