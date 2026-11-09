"""Tests for encryption at rest for secrets."""

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.crypto import (
    CIPHERTEXT_PREFIX,
    decrypt_secret,
    derive_key,
    encrypt_secret,
    is_encrypted,
)
from app.core.settings_store import load_into_settings, masked_dump, save_settings
from app.db.models import AppSetting
from app.db.session import AsyncSessionLocal


def test_derive_key_deterministic() -> None:
    k1 = derive_key("same-seed")
    k2 = derive_key("same-seed")
    k3 = derive_key("different-seed")
    assert k1 == k2
    assert k1 != k3
    assert len(k1) == 44


def test_encrypt_decrypt_roundtrip() -> None:
    secret = "my-super-secret-api-token-12345"
    encrypted = encrypt_secret(secret)
    assert encrypted != secret
    assert is_encrypted(encrypted)
    assert encrypted.startswith(CIPHERTEXT_PREFIX)

    decrypted, was_enc = decrypt_secret(encrypted)
    assert was_enc is True
    assert decrypted == secret


def test_encrypt_idempotent() -> None:
    secret = "my-secret"
    enc1 = encrypt_secret(secret)
    enc2 = encrypt_secret(enc1)
    assert enc1 == enc2


def test_decrypt_legacy_plaintext_fallback() -> None:
    legacy_secret = "old-plaintext-password"
    decrypted, was_enc = decrypt_secret(legacy_secret)
    assert was_enc is False
    assert decrypted == legacy_secret


def test_decrypt_empty() -> None:
    assert decrypt_secret("")[0] == ""
    assert encrypt_secret("") == ""


def test_decrypt_tampered_fails() -> None:
    secret = "test-secret"
    encrypted = encrypt_secret(secret)
    tampered = encrypted[:-5] + "XXXXX"
    with pytest.raises(ValueError, match="Cannot decrypt"):
        decrypt_secret(tampered)


@pytest.mark.asyncio
async def test_settings_store_encrypts_secrets_in_db() -> None:
    raw_api_key = "super-unifi-key-999"
    await save_settings({"unifi_api_key": raw_api_key, "sync_interval_seconds": 120})

    # 1. Verify in database: row value MUST be encrypted
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(select(AppSetting).where(AppSetting.key == "unifi_api_key"))
        ).scalar_one()
        assert row.is_secret is True
        assert row.value is not None
        assert row.value != raw_api_key
        assert is_encrypted(row.value)
        assert row.value.startswith(CIPHERTEXT_PREFIX)

    # 2. Verify masked dump in UI/API does not leak ciphertext or plaintext
    dump = masked_dump()
    assert dump["unifi_api_key"] == "***saved***"

    # 3. Verify load_into_settings decrypts into memory
    assert settings.unifi_api_key == raw_api_key


@pytest.mark.asyncio
async def test_auto_migration_of_legacy_plaintext_secret() -> None:
    from app.core.settings_store import clear_settings

    await clear_settings()
    legacy_key = "legacy-unencrypted-secret"
    async with AsyncSessionLocal() as session:
        session.add(AppSetting(key="unifi_api_key", value=legacy_key, is_secret=True))
        await session.commit()

    # Call load_into_settings -> should upgrade on the fly
    await load_into_settings()

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(select(AppSetting).where(AppSetting.key == "unifi_api_key"))
        ).scalar_one()
        assert row.value is not None
        assert row.value != legacy_key
        assert is_encrypted(row.value)
        decrypted, was_enc = decrypt_secret(row.value)
        assert was_enc is True
        assert decrypted == legacy_key
