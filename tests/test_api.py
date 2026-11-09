"""End-to-end API tests for folders, targets, settings and WireGuard endpoints."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import settings
from app.db.models import ResolvedIP, Target
from app.db.session import AsyncSessionLocal


async def _create_target(client: httpx.AsyncClient, **payload: Any) -> dict[str, Any]:
    response = await client.post("/api/v1/targets", json={"value": "198.51.100.0/24", **payload})
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


@pytest.mark.asyncio
async def test_folder_lifecycle_and_target_assignment(client: httpx.AsyncClient) -> None:
    """Folders can be created, renamed, listed and deleted without losing targets."""
    created = await client.post("/api/v1/folders", json={"name": "Personal", "color": "#10b981"})
    assert created.status_code == 201
    folder = created.json()
    folder_id = folder["id"]
    assert folder["targets_count"] == 0

    duplicate = await client.post("/api/v1/folders", json={"name": "personal"})
    assert duplicate.status_code == 409

    target = await _create_target(client, folder_id=folder_id, description="RFC 5737 block")
    assert target["folder_id"] == folder_id
    assert target["folder_name"] == "Personal"

    listed = await client.get("/api/v1/folders")
    assert listed.status_code == 200
    assert listed.json()[0]["targets_count"] == 1

    patched = await client.patch(f"/api/v1/folders/{folder_id}", json={"name": "Lists"})
    assert patched.status_code == 200
    assert patched.json()["name"] == "Lists"

    deleted = await client.delete(f"/api/v1/folders/{folder_id}")
    assert deleted.status_code == 204

    targets = await client.get("/api/v1/targets")
    assert targets.status_code == 200
    assert targets.json()[0]["folder_id"] is None
    assert targets.json()[0]["folder_name"] is None


@pytest.mark.asyncio
async def test_patch_target_recomputes_destination_type(client: httpx.AsyncClient) -> None:
    """Changing a target value re-detects CIDR vs FQDN and toggles the enabled flag."""
    target = await _create_target(client, value="sport-stream.example.org")
    assert target["destination_type"] == "fqdn"

    response = await client.patch(
        f"/api/v1/targets/{target['id']}",
        json={"value": "203.0.113.0/24", "enabled": False, "description": "switched"},
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["destination_type"] == "cidr"
    assert updated["enabled"] is False
    assert updated["description"] == "switched"

    missing = await client.patch("/api/v1/targets/unknown-id", json={"enabled": True})
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_bulk_import_deduplicates_and_reports_invalid(client: httpx.AsyncClient) -> None:
    """Bulk import creates valid entries, skips duplicates and reports invalid lines."""
    await _create_target(client, value="198.51.100.0/24")

    response = await client.post(
        "/api/v1/targets/bulk",
        json={
            "entries": [
                "198.51.100.0/24\nexample.org\nnot a target\n# comment\n192.0.2.7",
                "example.org",
            ],
            "description": "bulk list",
        },
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["created"] == 2  # example.org and 192.0.2.7; the CIDR already existed
    assert report["skipped"] == 2
    assert report["invalid"] == ["not a target"]

    targets = (await client.get("/api/v1/targets")).json()
    values = {item["value"] for item in targets}
    assert {"198.51.100.0/24", "example.org", "192.0.2.7/32"} <= values
    assert all(
        item["description"] == "bulk list" for item in targets if item["value"] != "198.51.100.0/24"
    )


@pytest.mark.asyncio
async def test_list_targets_returns_only_unexpired_resolved_ips(
    client: httpx.AsyncClient,
) -> None:
    """GET /targets exposes active resolved IPs and ignores expired ones."""
    created = await _create_target(client, value="feed.example.net")
    now = datetime.now(UTC)

    async with AsyncSessionLocal() as session:
        session.add_all(
            [
                ResolvedIP(
                    target_id=created["id"],
                    ip_address="198.51.100.50",
                    cidr="198.51.100.50/32",
                    expires_at=now + timedelta(hours=1),
                ),
                ResolvedIP(
                    target_id=created["id"],
                    ip_address="203.0.113.99",
                    cidr="203.0.113.99/32",
                    expires_at=now - timedelta(hours=1),
                ),
            ]
        )
        await session.commit()

    payload = (await client.get("/api/v1/targets")).json()
    assert payload[0]["resolved_ips"] == ["198.51.100.50"]
    assert payload[0]["resolved_count"] == 1


@pytest.mark.asyncio
async def test_resolve_endpoint_returns_cidr_for_cidr_target(client: httpx.AsyncClient) -> None:
    """A CIDR target resolves to itself without touching DNS."""
    created = await _create_target(client, value="192.0.2.0/24")

    response = await client.post(f"/api/v1/targets/{created['id']}/resolve")
    assert response.status_code == 200
    assert response.json() == {"value": "192.0.2.0/24", "ips": ["192.0.2.0/24"]}


@pytest.mark.asyncio
async def test_settings_endpoints_mask_secrets_and_hot_apply(client: httpx.AsyncClient) -> None:
    """PUT /settings persists values, masks secrets and rebuilds the cached driver."""
    response = await client.put(
        "/api/v1/settings",
        json={"unifi_api_key": "placeholder-key", "sync_interval_seconds": "120"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["unifi_api_key"] == "***saved***"
    assert "placeholder-key" not in str(body)
    assert body["sync_interval_seconds"] == 120

    fetched = await client.get("/api/v1/settings")
    assert fetched.status_code == 200
    assert fetched.json()["unifi_api_key"] == "***saved***"
    assert settings.sync_interval_seconds == 120

    invalid = await client.put("/api/v1/settings", json={"sync_interval_seconds": "-1"})
    assert invalid.status_code == 400

    probe = await client.post("/api/v1/settings/test-router")
    assert probe.status_code == 200
    assert probe.json()["driver"] == "mock"
    assert probe.json()["healthy"] is True


@pytest.mark.asyncio
async def test_wireguard_upload_preview_and_delete(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """wg0.conf is validated, stored with 0600 permissions and previewed redacted."""
    config_path = tmp_path / "wireguard" / "wg0.conf"
    monkeypatch.setattr(settings, "wireguard_config_path", str(config_path))

    empty = await client.get("/api/v1/gateway/wireguard")
    assert empty.status_code == 200
    assert empty.json()["configured"] is False

    invalid = await client.post("/api/v1/gateway/wireguard", json={"content": "[Peer]\n"})
    assert invalid.status_code == 400

    content = "\n".join(
        [
            "[Interface]",
            "Address = 192.0.2.2/32",
            "PrivateKey = placeholder-private-key",
            "DNS = 192.0.2.1",
            "",
            "[Peer]",
            "PublicKey = placeholder-public-key",
            "PresharedKey = placeholder-preshared-key",
            "Endpoint = 198.51.100.1:51820",
            "AllowedIPs = 192.0.2.0/24",
        ]
    )
    uploaded = await client.post("/api/v1/gateway/wireguard", json={"content": content})
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["peers"] == 1
    assert config_path.exists()
    assert (config_path.stat().st_mode & 0o777) == 0o600

    status = await client.get("/api/v1/gateway/wireguard")
    assert status.status_code == 200
    body = status.json()
    assert body["configured"] is True
    assert body["peers"] == 1
    assert body["uploaded_at"] is not None
    assert "[Interface]" in body["preview"]
    assert "placeholder-private-key" not in body["preview"]

    removed = await client.delete("/api/v1/gateway/wireguard")
    assert removed.status_code == 200
    assert removed.json()["configured"] is False
    assert not config_path.exists()


@pytest.mark.asyncio
async def test_feed_toggle_flips_enabled_flag(client: httpx.AsyncClient) -> None:
    """Feed toggle switches the enabled flag without deleting the feed."""
    created = await client.post(
        "/api/v1/feeds",
        json={
            "name": "HayAHora",
            "url": "https://hayahora.example.org/estado/data.json",
            "format": "hayahora",
        },
    )
    assert created.status_code == 201, created.text
    feed_id = created.json()["id"]
    assert created.json()["enabled"] is True

    toggled = await client.post(f"/api/v1/feeds/{feed_id}/toggle")
    assert toggled.status_code == 200
    assert toggled.json()["enabled"] is False

    restored = await client.post(f"/api/v1/feeds/{feed_id}/toggle")
    assert restored.json()["enabled"] is True


@pytest.mark.asyncio
async def test_targets_are_routed_through_driver(client: httpx.AsyncClient) -> None:
    """A CIDR target reaches the cached driver after a manual sync."""
    await _create_target(client, value="203.0.113.0/24")

    sync = await client.post("/api/v1/sync", json={"force": True})
    assert sync.status_code == 200
    assert sync.json()["success"] is True

    active = await client.get("/api/v1/routes/active")
    assert active.status_code == 200
    assert "203.0.113.0/24" in active.json()

    async with AsyncSessionLocal() as session:
        stored = (await session.execute(Target.__table__.select())).all()
    assert len(stored) == 1
