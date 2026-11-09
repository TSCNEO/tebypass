"""RouterOS HTTP contract: owned rules, complete policy and rejected writes."""

import json
from typing import Any

import httpx

from app.core.config import Settings
from app.drivers.base import TargetCIDR
from app.drivers.mikrotik import MikroTikRouterDriver


async def test_routeros_policy_creates_updates_and_tears_down_owned_rules() -> None:
    driver = MikroTikRouterDriver(
        Settings(
            mikrotik_api_url="https://router.example.net",
            mikrotik_username="test",
            mikrotik_password="test",
            mikrotik_routing_table="egress",
        )
    )
    records: list[dict[str, Any]] = [
        {".id": "*other", "comment": "other", "dst-address": "203.0.113.8/32"}
    ]

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("routing/table"):
            return httpx.Response(200, json=[{"name": "egress", "fib": "true"}])
        if request.method == "GET":
            return httpx.Response(200, json=records)
        if request.method == "PUT":
            records.append({".id": "*managed", **json.loads(request.content)})
            return httpx.Response(201, json=records[-1])
        if request.method == "PATCH":
            records[-1].update(json.loads(request.content))
            return httpx.Response(200, json=records[-1])
        if request.method == "DELETE":
            assert request.url.path.endswith("*managed")
            records.pop()
            return httpx.Response(204)
        return httpx.Response(400)

    driver._client = lambda: httpx.AsyncClient(  # type: ignore[method-assign]
        base_url="https://router.example.net/rest/", transport=httpx.MockTransport(handle)
    )
    targets = {TargetCIDR("198.51.100.7/32", "example")}
    first = await driver.apply_policy(targets, True)
    assert first.success and first.added_count == 1
    assert (await driver.apply_policy(targets, True)).success
    await driver.teardown()
    assert len(records) == 1 and records[0]["comment"] == "other"


async def test_routeros_rejected_write_is_not_success() -> None:
    driver = MikroTikRouterDriver()
    result = await driver.apply_policy({TargetCIDR("198.51.100.7/32", "test")}, True)
    assert not result.success
    assert result.total_active == -1
