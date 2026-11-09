"""Tests for the SQLite-backed settings store and secret masking."""

import json

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.settings_store import (
    EDITABLE_KEYS,
    MASKED_SECRET,
    SECRET_KEYS,
    load_into_settings,
    masked_dump,
    save_settings,
)
from app.db.models import AppSetting
from app.db.session import AsyncSessionLocal


@pytest.mark.asyncio
async def test_editable_keys_exclude_bootstrap_fields() -> None:
    """Bootstrap values must never be editable from the API."""
    assert "host" not in EDITABLE_KEYS
    assert "port" not in EDITABLE_KEYS
    assert "database_url" not in EDITABLE_KEYS
    assert {"sync_interval_seconds", "feed_force_active"} <= set(EDITABLE_KEYS)
    assert EDITABLE_KEYS["sync_interval_seconds"] is int
    assert EDITABLE_KEYS["gateway_fail_closed"] is bool


@pytest.mark.asyncio
async def test_masked_dump_never_returns_secret(db_ready: None) -> None:
    """A stored secret is masked in the dump and applied to the live settings object."""
    await save_settings({"unifi_api_key": "placeholder-test-key", "unifi_site": "default"})

    dump = masked_dump()
    assert dump["unifi_api_key"] == MASKED_SECRET
    assert "unifi_api_key" in SECRET_KEYS
    assert "placeholder-test-key" not in json.dumps(dump)
    assert settings.unifi_api_key == "placeholder-test-key"


@pytest.mark.asyncio
async def test_save_settings_ignores_masked_and_empty_secret(db_ready: None) -> None:
    """The API mask and empty strings must not overwrite the stored secret."""
    await save_settings({"unifi_password": "placeholder-password"})
    await save_settings({"unifi_password": MASKED_SECRET})
    await save_settings({"unifi_password": ""})
    await save_settings({"unifi_password": None})

    assert settings.unifi_password == "placeholder-password"


@pytest.mark.asyncio
async def test_save_settings_ignores_unknown_keys(db_ready: None) -> None:
    """Unknown keys are dropped instead of being persisted."""
    await save_settings({"totally_unknown_key": "value", "router_driver": "virtual_gateway"})

    assert settings.router_driver == "virtual_gateway"
    async with AsyncSessionLocal() as session:
        stored = (await session.execute(select(AppSetting.key))).scalars().all()
    assert "totally_unknown_key" not in stored
    assert "router_driver" in stored


@pytest.mark.asyncio
async def test_save_settings_coerces_types(db_ready: None) -> None:
    """Strings coming from JSON are coerced to the declared setting type."""
    await save_settings(
        {
            "sync_interval_seconds": "120",
            "feed_force_active": "false",
            "feed_isp_filter": "ISP_A",
        }
    )

    assert settings.sync_interval_seconds == 120
    assert settings.feed_force_active is False
    assert settings.feed_isp_filter == "ISP_A"

    with pytest.raises(ValueError):
        await save_settings({"sync_interval_seconds": "-5"})
    with pytest.raises(ValueError):
        await save_settings({"sliding_window_seconds": "not-a-number"})


@pytest.mark.asyncio
async def test_load_into_settings_restores_persisted_values(db_ready: None) -> None:
    """Persisted rows override whatever is currently held in memory."""
    await save_settings({"sliding_window_seconds": 900, "unifi_group_name": "Lab_Group"})

    settings.sliding_window_seconds = 60
    settings.unifi_group_name = "Overwritten"

    await load_into_settings()

    assert settings.sliding_window_seconds == 900
    assert settings.unifi_group_name == "Lab_Group"
