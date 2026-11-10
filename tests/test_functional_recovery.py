"""Adversarial acceptance checks for ingestion, driver switching and recovery."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.feeds_sync import refresh_feed
from app.core.settings_store import save_settings
from app.db.models import Feed, ResolvedIP, Target
from app.db.session import AsyncSessionLocal
from app.drivers.mock import MockRouterDriver
from app.drivers.virtual_gateway import VirtualGatewayDriver
from gateway.wireguard_runtime import WireGuardRuntime

VALID = (
    "[Interface]\nPrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\n"
    "Address = 192.0.2.2/32\n[Peer]\n"
    "PublicKey = AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=\n"
    "AllowedIPs = 198.51.100.0/24\nEndpoint = edge.example.net:51820\n"
)


@pytest.mark.parametrize("driver", ["virtual_gateway", "mikrotik", "omada"])
async def test_unsupported_ipv6_rejected_by_create_patch_and_bulk(
    client: httpx.AsyncClient,
    driver: str,
) -> None:
    settings.router_driver = driver  # type: ignore[assignment]
    # Keep the test on ingestion: an IPv4 target need not contact an actual router.
    with patch("app.core.routes_service._trigger_background_reconciliation", AsyncMock()):
        created = await client.post("/api/v1/targets", json={"value": "198.51.100.7"})
        assert created.status_code == 201
        target_id = created.json()["id"]
        patched = await client.patch(f"/api/v1/targets/{target_id}", json={"value": "2001:db8::7"})
        assert patched.status_code == 400
        refused = await client.post("/api/v1/targets", json={"value": "2001:db8::7"})
        assert refused.status_code == 400
        bulk = await client.post(
            "/api/v1/targets/bulk", json={"entries": ["2001:db8::7", "203.0.113.7"]}
        )
        assert bulk.json()["created"] == 1
        assert bulk.json()["invalid"] == ["2001:db8::7"]
    assert {row["value"] for row in (await client.get("/api/v1/targets")).json()} == {
        "198.51.100.7/32",
        "203.0.113.7/32",
    }


@pytest.mark.parametrize("driver", ["virtual_gateway", "mikrotik", "omada"])
async def test_bad_feed_preserves_signal_identity_and_snapshot(db_ready: None, driver: str) -> None:
    settings.router_driver = driver  # type: ignore[assignment]
    async with AsyncSessionLocal() as session:
        feed = Feed(
            name="Example", url="https://feed.example.net/list", format="plaintext", blocked=True
        )
        session.add(feed)
        await session.flush()
        target = Target(value="198.51.100.7/32", destination_type="cidr", feed_id=feed.id)
        session.add(target)
        await session.commit()
        with patch(
            "app.core.feeds_sync.download",
            AsyncMock(return_value=("203.0.113.7\n2001:db8::7", None)),
        ):
            await refresh_feed(session, feed)
        assert feed.last_error
        assert feed.blocked is True
        targets = (await session.execute(select(Target))).scalars().all()
        assert len(targets) == 1 and targets[0].id == target.id


@pytest.mark.parametrize("retained", [False, True])
async def test_driver_change_rejects_existing_ipv6_before_teardown(
    db_ready: None, retained: bool
) -> None:
    async with AsyncSessionLocal() as session:
        target = Target(
            value="edge.example.net" if retained else "2001:db8::7/128",
            destination_type="fqdn" if retained else "cidr",
        )
        session.add(target)
        await session.flush()
        if retained:
            session.add(
                ResolvedIP(
                    target_id=target.id,
                    ip_address="2001:db8::7",
                    cidr="2001:db8::7/128",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
        await session.commit()
    driver = MockRouterDriver()
    with (
        patch("app.drivers.get_driver", return_value=driver),
        patch.object(driver, "teardown", AsyncMock()) as teardown,
    ):
        with pytest.raises(ValueError, match="IPv4"):
            await save_settings({"router_driver": "mikrotik"})
        teardown.assert_not_awaited()
    assert settings.router_driver == "mock"


@pytest.mark.parametrize("failure", ["up", "route"])
async def test_wireguard_recovers_running_tunnel_after_update_failure(
    tmp_path: Path, failure: str
) -> None:
    running = True
    fail_once = True
    calls: list[tuple[str, ...]] = []

    async def run(*args: str) -> tuple[int, str, str]:
        nonlocal running, fail_once
        calls.append(args)
        if args[:2] == ("wg-quick", "down"):
            running = False
        elif args[:2] == ("wg-quick", "up"):
            if failure == "up" and fail_once:
                fail_once = False
                return 1, "", "injected failure"
            running = True
        elif args[:3] == ("ip", "route", "replace") and failure == "route" and fail_once:
            fail_once = False
            return 1, "", "injected failure"
        return 0, "", ""

    runtime = WireGuardRuntime(run)
    runtime.source = tmp_path / "wg0.conf"
    runtime.active = tmp_path / "active.conf"
    runtime.source.write_text(VALID)
    previous = VALID.replace("192.0.2.2/32", "192.0.2.3/32")
    runtime.active.write_text(previous)
    with (
        patch.object(runtime, "is_running", side_effect=lambda: running),
        pytest.raises(RuntimeError, match="previous state restored"),
    ):
        await runtime.reload()
    assert running
    assert runtime.active.read_text() == previous
    assert calls[-1][:3] == ("ip", "route", "replace")
    assert runtime.active.stat().st_mode & 0o777 == 0o600


async def test_wireguard_reports_failed_rollback_and_keeps_previous_file(tmp_path: Path) -> None:
    runtime = WireGuardRuntime(AsyncMock(return_value=(1, "", "failed")))
    runtime.source = tmp_path / "wg0.conf"
    runtime.active = tmp_path / "active.conf"
    runtime.source.write_text(VALID)
    previous = VALID.replace("192.0.2.2/32", "192.0.2.3/32")
    runtime.active.write_text(previous)
    with (
        patch.object(runtime, "is_running", return_value=True),
        patch.object(runtime, "stop", AsyncMock()),
        pytest.raises(RuntimeError, match="rollback failed"),
    ):
        await runtime.reload()
    assert runtime.active.read_text() == previous


async def test_wireguard_last_good_survives_runtime_recreation(tmp_path: Path) -> None:
    running = False
    reject_candidate = False

    async def run(*args: str) -> tuple[int, str, str]:
        nonlocal running
        if args[:2] == ("wg-quick", "up"):
            if reject_candidate and "192.0.2.4/32" in Path(args[2]).read_text():
                return 1, "", "failed"
            running = True
        elif args[:2] == ("wg-quick", "down"):
            running = False
        return 0, "", ""

    runtime = WireGuardRuntime(run)
    runtime.source = tmp_path / "wg0.conf"
    runtime.active = tmp_path / "active.conf"
    runtime.source.write_text(VALID)
    with patch.object(runtime, "is_running", side_effect=lambda: running):
        await runtime.reload()
    backup = tmp_path / "wg0.last-good.conf"
    assert backup.is_file() and backup.stat().st_mode & 0o777 == 0o600
    runtime.active.unlink()
    running = False
    reject_candidate = True
    runtime.source.write_text(VALID.replace("192.0.2.2/32", "192.0.2.4/32"))
    restarted = WireGuardRuntime(run)
    restarted.source, restarted.active = runtime.source, runtime.active
    with patch.object(restarted, "is_running", side_effect=lambda: running):
        with pytest.raises(RuntimeError, match="previous state restored"):
            await restarted.reload()
        assert running and "192.0.2.2/32" in restarted.active.read_text()
        restarted.source.unlink()
        await restarted.reload()
    assert not running and not backup.exists() and not restarted.active.exists()


@pytest.mark.parametrize(
    "ack", [None, {"ok": True}, {"ok": True, "verified": True, "fail_closed": False}]
)
async def test_gateway_rejects_missing_or_wrong_policy_ack(ack: dict[str, object] | None) -> None:
    settings.gateway_fail_closed = True
    driver = VirtualGatewayDriver()
    with patch.object(driver, "_socket_request", AsyncMock(return_value=ack)) as request:
        with pytest.raises(RuntimeError, match="policy"):
            await driver.apply_policy(set(), False)
        assert request.await_count == 1


@pytest.mark.parametrize("fail_closed", [True, False])
async def test_policy_observes_exact_mark_table_and_fallback(fail_closed: bool) -> None:
    rules = [{"priority": 100, "fwmark": "0x100", "table": 100}]
    routes = [{"type": "unreachable", "dst": "default", "metric": 32760}] if fail_closed else []
    runtime = WireGuardRuntime(
        AsyncMock(side_effect=[(0, json.dumps(rules), ""), (0, json.dumps(routes), "")])
    )
    assert await runtime.policy_matches(fail_closed)
    rules[0]["table"] = 200
    runtime.run = AsyncMock(side_effect=[(0, json.dumps(rules), ""), (0, json.dumps(routes), "")])
    assert not await runtime.policy_matches(fail_closed)
