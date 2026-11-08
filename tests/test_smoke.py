"""Smoke and unit verification suite for TeByPass core components."""

import pytest

from app.core.cache import SlidingWindowCache
from app.core.config import Settings
from app.core.feeds import FeedManager
from app.drivers.base import DriverHealth, TargetCIDR
from app.drivers.mock import MockRouterDriver


def test_settings_defaults() -> None:
    """Validate default configuration values."""
    settings = Settings()
    assert settings.port == 9384
    assert settings.router_driver == "mock"
    assert settings.sliding_window_seconds > 0


@pytest.mark.asyncio
async def test_sliding_window_cache() -> None:
    """Test entry insertion, retrieval, and active CIDRs."""
    cache = SlidingWindowCache(default_window_seconds=3600)

    # Store IP for domain
    entry = await cache.add_or_extend(
        fqdn="sport-stream.example.org",
        ip_address="198.51.100.50",
        cidr="198.51.100.50/32",
        dns_ttl=300,
    )
    assert entry.fqdn == "sport-stream.example.org"
    assert entry.ip_address == "198.51.100.50"

    active_cidrs = await cache.get_active_cidrs()
    assert "198.51.100.50/32" in active_cidrs

    entries = await cache.get_entries_for_fqdn("sport-stream.example.org")
    assert len(entries) == 1
    assert entries[0].ip_address == "198.51.100.50"


@pytest.mark.asyncio
async def test_mock_router_driver() -> None:
    """Test MockRouterDriver lifecycle and routing operations."""
    driver = MockRouterDriver()
    await driver.initialize()

    status = await driver.health_check()
    assert status.health == DriverHealth.HEALTHY

    # Apply route changes
    target = TargetCIDR(
        cidr="198.51.100.0/24",
        target_id="test-target-1",
        description="RFC 5737 Benchmark test prefix",
    )
    result = await driver.sync_routes(to_add={target}, to_remove=set())
    assert result.success is True
    assert result.added_count == 1

    routes = await driver.get_current_routes()
    assert "198.51.100.0/24" in routes

    await driver.close()


def test_feed_manager_json_payload() -> None:
    """Test parsing of JSON feed format using FeedManager."""
    manager = FeedManager()
    sample_json = """
    {
        "status": "ok",
        "domains": ["feed.example.net", "sport-stream.example.org"],
        "ips": ["198.51.100.25", "203.0.113.10"]
    }
    """
    targets = manager.parse_content(sample_json, feed_format="json")
    values = [t.value for t in targets]

    assert "feed.example.net" in values
    assert "sport-stream.example.org" in values
    assert "198.51.100.25/32" in values
    assert "203.0.113.10/32" in values


def test_dynamic_version_info() -> None:
    """Validate dynamic version info generation."""
    from app.core.version import get_version_info, get_version_string

    info = get_version_info()
    assert "base_version" in info
    assert "commit" in info
    assert "display_version" in info

    ver_str = get_version_string()
    assert len(ver_str) > 0
    assert ver_str.startswith("v")
