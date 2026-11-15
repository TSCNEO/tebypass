"""Real HTTP-boundary contract tests for StopligaAdapter (OPNsense & Omada).

These tests exercise StopligaAdapter + upstream stopliga HTTP parsing, validation,
group/alias mutation, rule binding verification, rollback/error handling, and teardown
WITHOUT mocking `_observe`, `get_current_routes`, or `backend.sync`.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from email.message import Message
from typing import Any

import pytest

from app.core.config import Settings
from app.drivers.base import DriverHealth, TargetCIDR
from app.drivers.stopliga_adapter import StopligaAdapter


class _FakeHTTPResponse:
    """Minimal context-managed response object compatible with urllib + read_limited."""

    def __init__(
        self,
        status: int,
        body: dict[str, Any] | list[Any] | str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        raw_bytes = (
            body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
        )
        self._stream = io.BytesIO(raw_bytes)
        msg = Message()
        msg["Content-Length"] = str(len(raw_bytes))
        if headers:
            for k, v in headers.items():
                msg[k] = v
        self.headers = msg

    def read(self, n: int = -1) -> bytes:
        return self._stream.read(n)

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _decode_json_body(req: urllib.request.Request) -> dict[str, Any]:
    raw = req.data
    if raw is None:
        return {}
    assert isinstance(raw, (bytes, bytearray))
    parsed = json.loads(raw.decode("utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _http_error(url: str, code: int, body: dict[str, Any] | str) -> urllib.error.HTTPError:
    raw = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
    hdrs = Message()
    hdrs["Content-Length"] = str(len(raw))
    return urllib.error.HTTPError(url, code, f"HTTP {code}", hdrs, io.BytesIO(raw))


class _FakeOPNsenseServer:
    """Stateful fake OPNsense API endpoint handler."""

    def __init__(self, *, rule_gateway: str = "WG_VPN_GW") -> None:
        self.requests: list[tuple[str, str, dict[str, Any] | None]] = []
        self.aliases: dict[str, dict[str, Any]] = {}
        self.rules: dict[str, dict[str, Any]] = {
            "rule-uuid-1": {
                "uuid": "rule-uuid-1",
                "description": "TeByPass",
                "enabled": "0",
                "action": {"pass": {"value": "Pass", "selected": "1"}},
                "direction": {"in": {"value": "in", "selected": "1"}},
                "destination_net": "TeByPass_Targets",
                "destination_not": "0",
                "gateway": {rule_gateway: {"value": rule_gateway, "selected": "1"}},
            }
        }
        self._next_alias_num = 1
        self.fail_next_reconfigure = False

    def handle(self, req: urllib.request.Request) -> _FakeHTTPResponse:
        method = req.get_method().upper()
        full_url = req.full_url
        path = full_url.split("/api", 1)[-1]
        body_obj = _decode_json_body(req) if req.data else None
        self.requests.append((method, path, body_obj))

        if path == "/firewall/alias/get":
            return _FakeHTTPResponse(200, {"aliases": {}})

        if path.startswith("/firewall/alias/searchItem"):
            rows = [
                {"uuid": uid, "name": data["name"], "enabled": data.get("enabled", "1")}
                for uid, data in self.aliases.items()
            ]
            return _FakeHTTPResponse(200, {"rows": rows, "total": len(rows)})

        if path == "/firewall/alias/addItem" and method == "POST":
            assert body_obj is not None
            alias_in = body_obj["alias"]
            uid = f"alias-uuid-{self._next_alias_num}"
            self._next_alias_num += 1
            self.aliases[uid] = dict(alias_in)
            return _FakeHTTPResponse(200, {"result": "saved", "uuid": uid})

        if path.startswith("/firewall/alias/getItem/"):
            uid = path.rsplit("/", 1)[-1]
            alias = self.aliases.get(uid)
            if alias is None:
                raise _http_error(full_url, 404, {"error": "not found"})
            return _FakeHTTPResponse(200, {"alias": alias})

        if path.startswith("/firewall/alias/setItem/") and method == "POST":
            uid = path.rsplit("/", 1)[-1]
            assert body_obj is not None
            self.aliases[uid] = dict(body_obj["alias"])
            return _FakeHTTPResponse(200, {"result": "saved"})

        if path.startswith("/firewall/alias/delItem/") and method == "POST":
            uid = path.rsplit("/", 1)[-1]
            self.aliases.pop(uid, None)
            return _FakeHTTPResponse(200, {"result": "deleted"})

        if path == "/firewall/alias/reconfigure" and method == "POST":
            if self.fail_next_reconfigure:
                self.fail_next_reconfigure = False
                raise _http_error(full_url, 500, {"status": "failed"})
            return _FakeHTTPResponse(200, {"status": "ok"})

        if path.startswith("/firewall/filter/searchRule"):
            rows = [
                {
                    "uuid": uid,
                    "description": r["description"],
                    "enabled": r["enabled"],
                }
                for uid, r in self.rules.items()
            ]
            return _FakeHTTPResponse(200, {"rows": rows, "total": len(rows)})

        if path.startswith("/firewall/filter/getRule/"):
            uid = path.rsplit("/", 1)[-1]
            rule = self.rules.get(uid)
            if rule is None:
                raise _http_error(full_url, 404, {"error": "not found"})
            return _FakeHTTPResponse(200, {"rule": rule})

        if path.startswith("/firewall/filter/toggleRule/") and method == "POST":
            parts = path.split("/")
            uid, state = parts[-2], parts[-1]
            self.rules[uid]["enabled"] = "1" if state == "1" else "0"
            return _FakeHTTPResponse(200, {"result": "saved"})

        if path == "/firewall/filter/apply" and method == "POST":
            return _FakeHTTPResponse(200, {"status": "ok"})

        raise _http_error(full_url, 404, {"error": f"Unhandled path {method} {path}"})


class _FakeFakeOpener:
    def __init__(self, handler: Callable[[urllib.request.Request], _FakeHTTPResponse]) -> None:
        self._handler = handler

    def open(self, req: urllib.request.Request, timeout: float | None = None) -> _FakeHTTPResponse:
        del timeout
        return self._handler(req)


async def test_opnsense_http_boundary_full_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise health_check, alias creation, update, disable, teardown over OPNsense HTTP."""
    server = _FakeOPNsenseServer()
    monkeypatch.setattr(
        urllib.request, "build_opener", lambda *a, **kw: _FakeFakeOpener(server.handle)
    )

    adapter = StopligaAdapter(
        Settings(
            router_driver="opnsense",
            opnsense_host="firewall.example.net",
            opnsense_api_key="key-1",
            opnsense_api_secret="sec-1",
            opnsense_alias_name="TeByPass_Targets",
            opnsense_route_name="TeByPass",
            opnsense_verify_tls=False,
        )
    )

    probe = await adapter.health_check()
    assert probe.health == DriverHealth.HEALTHY
    assert probe.details["ready"] is True
    assert probe.details["verification"] == "full"
    assert probe.details["rule_enabled"] is False

    apply_res = await adapter.apply_policy(
        {TargetCIDR("198.51.100.10/32", "t1"), TargetCIDR("198.51.100.11/32", "t2")},
        True,
    )
    assert apply_res.success is True
    assert apply_res.added_count == 2
    assert apply_res.total_active == 2
    assert server.rules["rule-uuid-1"]["enabled"] == "1"
    assert await adapter.get_current_routes(force=True) == {"198.51.100.10/32", "198.51.100.11/32"}

    # Update targets & verify diff
    update_res = await adapter.apply_policy({TargetCIDR("203.0.113.20/32", "t3")}, True)
    assert update_res.success is True
    assert update_res.total_active == 1
    assert await adapter.get_current_routes(force=True) == {"203.0.113.20/32"}

    # Teardown disables the rule and verifies absence of active routes
    await adapter.teardown()
    assert server.rules["rule-uuid-1"]["enabled"] == "0"
    assert await adapter.get_current_routes(force=True) == set()


async def test_opnsense_http_boundary_rejects_bad_rule_gateway_and_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrong gateway fails readiness probe; HTTP 500 on alias reconfigure triggers clean rollback."""
    bad_server = _FakeOPNsenseServer(rule_gateway="default")
    monkeypatch.setattr(
        urllib.request, "build_opener", lambda *a, **kw: _FakeFakeOpener(bad_server.handle)
    )
    bad_adapter = StopligaAdapter(
        Settings(
            router_driver="opnsense",
            opnsense_host="firewall.example.net",
            opnsense_api_key="key-1",
            opnsense_api_secret="sec-1",
            opnsense_alias_name="TeByPass_Targets",
            opnsense_route_name="TeByPass",
        )
    )
    bad_probe = await bad_adapter.health_check()
    assert bad_probe.health == DriverHealth.UNHEALTHY
    assert bad_probe.details["ready"] is False

    # Now test rollback when alias reconfigure fails mid-create on an otherwise valid firewall
    ok_server = _FakeOPNsenseServer(rule_gateway="WG_VPN_GW")
    ok_server.fail_next_reconfigure = True
    monkeypatch.setattr(
        urllib.request, "build_opener", lambda *a, **kw: _FakeFakeOpener(ok_server.handle)
    )
    adapter = StopligaAdapter(
        Settings(
            router_driver="opnsense",
            opnsense_host="firewall.example.net",
            opnsense_api_key="key-1",
            opnsense_api_secret="sec-1",
            opnsense_alias_name="TeByPass_Targets",
            opnsense_route_name="TeByPass",
        )
    )
    fail_res = await adapter.apply_policy({TargetCIDR("198.51.100.10/32", "t1")}, True)
    assert fail_res.success is False
    assert adapter.policy_write_started is True
    # stopliga's OPNsense rollback deletes the newly added orphan alias after reconfigure fails
    assert ok_server.aliases == {}


class _FakeOmadaServer:
    """Stateful fake Omada Open API v1/v3 controller."""

    def __init__(self) -> None:
        self.token_counter = 0
        self.current_token = "tok-1"
        self.force_401_once = True
        self.omadac_id = "ctrl-abc"
        self.site_id = "site-1"
        self.groups: dict[str, dict[str, Any]] = {}
        self.routes: dict[str, dict[str, Any]] = {}
        self._next_group = 1
        self._next_route = 1
        self.requests: list[tuple[str, str]] = []

    def handle(
        self, req: urllib.request.Request, context: Any = None, timeout: float | None = None
    ) -> _FakeHTTPResponse:
        del context, timeout
        method = req.get_method().upper()
        url = req.full_url
        path_and_query = "/" + url.split("://", 1)[1].split("/", 1)[1]
        path = path_and_query.split("?", 1)[0]
        self.requests.append((method, path))

        if path == "/openapi/authorize/token":
            self.token_counter += 1
            self.current_token = f"tok-{self.token_counter}"
            return _FakeHTTPResponse(200, {"accessToken": self.current_token})

        auth_header = req.headers.get("Authorization", "")
        if auth_header != f"AccessToken={self.current_token}":
            raise _http_error(url, 401, {"errorCode": -44112, "msg": "invalid token"})
        if self.force_401_once and path.endswith("/sites"):
            self.force_401_once = False
            raise _http_error(url, 401, {"errorCode": -44112, "msg": "expired token"})

        prefix = f"/openapi/v1/{self.omadac_id}"
        if path == f"{prefix}/sites":
            rows = [{"siteId": self.site_id, "name": "Default"}]
            return _FakeHTTPResponse(
                200, {"errorCode": 0, "result": {"data": rows, "totalRows": 1}}
            )

        if path == f"/openapi/v3/{self.omadac_id}/sites/{self.site_id}/lan-networks":
            return _FakeHTTPResponse(
                200, {"errorCode": 0, "result": {"data": [{"id": "lan-1", "name": "LAN"}]}}
            )

        if path == f"{prefix}/sites/{self.site_id}/vpn/site-to-site-vpns":
            return _FakeHTTPResponse(200, {"errorCode": 0, "result": {"data": []}})

        if path == f"{prefix}/sites/{self.site_id}/vpn/client-to-site-vpn-clients":
            return _FakeHTTPResponse(
                200,
                {
                    "errorCode": 0,
                    "result": {"data": [{"id": "vpn-wg-1", "name": "WG-Exit"}]},
                },
            )

        if path == f"{prefix}/sites/{self.site_id}/profiles/groups":
            if method == "GET":
                return _FakeHTTPResponse(
                    200, {"errorCode": 0, "result": {"data": list(self.groups.values())}}
                )
            if method == "POST":
                body = _decode_json_body(req)
                gid = f"grp-{self._next_group}"
                self._next_group += 1
                self.groups[gid] = {**body, "groupId": gid}
                return _FakeHTTPResponse(200, {"errorCode": 0, "result": {"id": gid}})

        if path.startswith(f"{prefix}/sites/{self.site_id}/profiles/groups/0/"):
            gid = path.rsplit("/", 1)[-1]
            if method == "PATCH":
                body = _decode_json_body(req)
                self.groups[gid] = {**body, "groupId": gid}
                return _FakeHTTPResponse(200, {"errorCode": 0, "result": {}})
            if method == "DELETE":
                self.groups.pop(gid, None)
                return _FakeHTTPResponse(200, {"errorCode": 0, "result": {}})

        if path == f"{prefix}/sites/{self.site_id}/routing/policy-routings":
            if method == "GET":
                rows = list(self.routes.values())
                return _FakeHTTPResponse(
                    200, {"errorCode": 0, "result": {"data": rows, "totalRows": len(rows)}}
                )
            if method == "POST":
                body = _decode_json_body(req)
                rid = f"route-{self._next_route}"
                self._next_route += 1
                self.routes[rid] = {**body, "id": rid}
                return _FakeHTTPResponse(200, {"errorCode": 0, "result": {"id": rid}})

        if path.startswith(f"{prefix}/sites/{self.site_id}/routing/policy-routings/"):
            rid = path.rsplit("/", 1)[-1]
            if method == "PUT":
                body = _decode_json_body(req)
                self.routes[rid] = {**body, "id": rid}
                return _FakeHTTPResponse(200, {"errorCode": 0, "result": {}})

        raise _http_error(url, 404, {"errorCode": -1, "msg": f"unhandled {method} {path}"})


async def test_omada_http_boundary_full_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise Omada OAuth re-auth, group creation/collapse, update, and teardown over HTTP."""
    server = _FakeOmadaServer()
    monkeypatch.setattr(urllib.request, "urlopen", server.handle)

    adapter = StopligaAdapter(
        Settings(
            router_driver="omada",
            omada_base_url="https://omada.example.net",
            omada_client_id="cid-1",
            omada_client_secret="csec-1",
            omada_controller_id="ctrl-abc",
            omada_site="Default",
            omada_target_type="vpn",
            omada_target="WG-Exit",
            omada_route_name="TeByPass",
            omada_verify_tls=False,
        )
    )

    probe = await adapter.health_check()
    assert probe.health == DriverHealth.HEALTHY
    assert probe.details["ready"] is True
    assert server.token_counter >= 2  # initial auth + 401 re-auth retry succeeded

    # Adjacent IPs collapse cleanly into 198.51.100.0/31 inside Omada group & verify coverage
    res = await adapter.apply_policy(
        {TargetCIDR("198.51.100.0/32", "t1"), TargetCIDR("198.51.100.1/32", "t2")},
        True,
    )
    assert res.success is True
    assert adapter.policy_write_started is True
    assert await adapter.get_current_routes(force=True) == {"198.51.100.0/31"}
    assert len(server.routes) == 1
    route = next(iter(server.routes.values()))
    assert route["status"] is True
    assert route["vpnIds"] == ["vpn-wg-1"]

    # Update to another destination
    res2 = await adapter.apply_policy({TargetCIDR("203.0.113.5/32", "t3")}, True)
    assert res2.success is True
    assert await adapter.get_current_routes(force=True) == {"203.0.113.5/32"}

    # Teardown disables policy route in place and clears observed routes
    await adapter.teardown()
    assert next(iter(server.routes.values()))["status"] is False
    assert await adapter.get_current_routes(force=True) == set()


async def test_omada_http_boundary_reports_missing_vpn_target_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing configured Omada VPN target makes health_check UNHEALTHY cleanly."""
    server = _FakeOmadaServer()
    monkeypatch.setattr(urllib.request, "urlopen", server.handle)

    adapter = StopligaAdapter(
        Settings(
            router_driver="omada",
            omada_base_url="https://omada.example.net",
            omada_client_id="cid-1",
            omada_client_secret="csec-1",
            omada_controller_id="ctrl-abc",
            omada_site="Default",
            omada_target_type="vpn",
            omada_target="Missing-VPN",
            omada_route_name="TeByPass",
        )
    )
    probe = await adapter.health_check()
    assert probe.health == DriverHealth.UNHEALTHY
    assert probe.details["ready"] is False
