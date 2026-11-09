"""Adapter exercises upstream plans and verifies their observed effective state."""

from typing import Any
from unittest.mock import patch

import pytest

from app.core.config import Settings
from app.drivers.base import TargetCIDR
from app.drivers.stopliga_adapter import StopligaAdapter


@pytest.mark.parametrize("router", ["omada", "opnsense"])
async def test_adapter_reconciles_complete_policy_and_disables(router: str) -> None:
    cfg = Settings(router_driver=router)  # type: ignore[arg-type]
    driver = StopligaAdapter(cfg)
    observed: set[str] = set()

    def sync(snapshot: Any, guard: Any, **kwargs: Any) -> None:
        nonlocal observed
        observed = set(snapshot.destinations) if snapshot.desired_enabled else set()

    with (
        patch.object(driver, "_observe", side_effect=lambda: observed),
        patch.object(driver.backend, "sync", side_effect=sync),
    ):
        target = {TargetCIDR("198.51.100.7/32", "example")}
        result = await driver.apply_policy(target, True)
        assert result.success and result.total_active == 1
        await driver.teardown()
        assert not await driver.get_current_routes(force=True)


async def test_adapter_verification_rejects_successful_but_ineffective_write() -> None:
    driver = StopligaAdapter(Settings(router_driver="omada"))
    with (
        patch.object(driver, "_observe", return_value=set()),
        patch.object(driver.backend, "sync", return_value=None),
    ):
        result = await driver.apply_policy({TargetCIDR("198.51.100.7/32", "example")}, True)
    assert not result.success and result.total_active == -1
