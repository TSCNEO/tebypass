"""Backup and restore verification tests for SQLite WAL + Fernet encryption key (C09)."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import crypto, settings_store
from app.core.config import Settings
from app.db.models import AppSetting, Base


async def test_c09_sqlite_online_backup_and_restore_preserves_wal_and_encrypted_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify sqlite3.Connection.backup() captures uncommitted WAL data and restores cleanly."""
    source_dir = tmp_path / "source_data"
    source_dir.mkdir()
    source_db = source_dir / "tebypass.db"
    secret_key_path = source_dir / ".secret_key"
    secret_key = Fernet.generate_key()
    secret_key_path.write_bytes(secret_key)
    os.chmod(secret_key_path, 0o600)

    monkeypatch.delenv("TEBYPASS_SECRET_KEY", raising=False)
    monkeypatch.setenv("TEBYPASS_DATABASE_URL", f"sqlite+aiosqlite:///{source_db}")
    monkeypatch.setattr(crypto, "_CACHED_KEY", None)
    monkeypatch.setattr(crypto, "_CACHED_FERNET", None)

    src_engine = create_async_engine(f"sqlite+aiosqlite:///{source_db}")
    try:
        async with src_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        src_factory = async_sessionmaker(src_engine, expire_on_commit=False)
        async with src_factory() as session:
            await session.execute(text("PRAGMA journal_mode=WAL"))
            session.add_all(
                [
                    AppSetting(key="router_driver", value="mikrotik", is_secret=False),
                    AppSetting(
                        key="mikrotik_api_url",
                        value="https://router.example.net",
                        is_secret=False,
                    ),
                    AppSetting(
                        key="mikrotik_password",
                        value=crypto.encrypt_secret("super-secret-password"),
                        is_secret=True,
                    ),
                ]
            )
            await session.commit()

        # Perform online backup using Python sqlite3 .backup() while WAL files exist
        backup_db = tmp_path / "tebypass.backup.db"
        with sqlite3.connect(source_db) as src, sqlite3.connect(backup_db) as dst:
            src.backup(dst)
    finally:
        await src_engine.dispose()

    # Verify backup integrity standalone
    with sqlite3.connect(backup_db) as check_conn:
        integrity = check_conn.execute("PRAGMA integrity_check").fetchone()
        assert integrity == ("ok",)

    # Restore into a fresh directory alongside the backed-up .secret_key
    restore_dir = tmp_path / "restored_data"
    restore_dir.mkdir()
    restored_db = restore_dir / "tebypass.db"
    restored_db.write_bytes(backup_db.read_bytes())
    restored_key = restore_dir / ".secret_key"
    restored_key.write_bytes(secret_key_path.read_bytes())
    os.chmod(restored_key, 0o600)

    monkeypatch.setenv("TEBYPASS_DATABASE_URL", f"sqlite+aiosqlite:///{restored_db}")
    monkeypatch.setattr(crypto, "_CACHED_KEY", None)
    monkeypatch.setattr(crypto, "_CACHED_FERNET", None)

    restored_engine = create_async_engine(f"sqlite+aiosqlite:///{restored_db}")
    try:
        restored_factory = async_sessionmaker(restored_engine, expire_on_commit=False)
        isolated_settings = Settings()
        monkeypatch.setattr(settings_store, "AsyncSessionLocal", restored_factory)
        monkeypatch.setattr(settings_store, "settings", isolated_settings)
        await settings_store.load_into_settings()

        assert isolated_settings.router_driver == "mikrotik"
        assert isolated_settings.mikrotik_api_url == "https://router.example.net"
        assert isolated_settings.mikrotik_password == "super-secret-password"

        async with restored_factory() as verify_session:
            row = (
                await verify_session.execute(
                    select(AppSetting).where(AppSetting.key == "mikrotik_password")
                )
            ).scalar_one()
            assert row.value is not None
            decrypted, was_encrypted = crypto.decrypt_secret(row.value)
            assert was_encrypted is True
            assert decrypted == "super-secret-password"
    finally:
        await restored_engine.dispose()
