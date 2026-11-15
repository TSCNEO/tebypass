"""Database session and connection management."""

from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.targets import as_utc, dedupe_key

if settings.database_url:
    DATABASE_URL = settings.database_url
    if DATABASE_URL.startswith("sqlite+aiosqlite:///"):
        # Handle 4 slashes for absolute path or 3 slashes for relative path
        raw_path = DATABASE_URL.split("sqlite+aiosqlite:///")[-1]
        if raw_path:
            Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
else:
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    DATABASE_URL = f"sqlite+aiosqlite:///{settings.db_path}"

engine: AsyncEngine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
    """Apply performance and concurrency pragmas to SQLite on connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.execute("PRAGMA busy_timeout=5000;")
    cursor.close()


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields an active async database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def _parse_sqlite_dt(value: object) -> datetime:
    if isinstance(value, datetime):
        return as_utc(value)
    return as_utc(datetime.fromisoformat(str(value)))


async def _migrate_resolved_ips_uniqueness(conn: AsyncConnection) -> None:
    """Deduplicate pre-existing ResolvedIP rows keeping longest retention and enforce uniqueness."""
    cols = (await conn.execute(text("PRAGMA table_info(resolved_ips)"))).fetchall()
    if not cols:
        return

    indexes = (await conn.execute(text("PRAGMA index_list(resolved_ips)"))).fetchall()
    has_unique_pair = False
    for idx in indexes:
        if int(idx[2]) != 1:
            continue
        idx_name = str(idx[1]).replace('"', '""')
        idx_cols = [
            str(row[2])
            for row in (await conn.execute(text(f'PRAGMA index_info("{idx_name}")'))).fetchall()
        ]
        if set(idx_cols) == {"target_id", "ip_address"} and len(idx_cols) == 2:
            has_unique_pair = True
            break

    if has_unique_pair:
        return

    duplicates = (
        await conn.execute(
            text(
                "SELECT target_id, ip_address FROM resolved_ips "
                "GROUP BY target_id, ip_address HAVING COUNT(*) > 1"
            )
        )
    ).fetchall()

    for target_id, ip_address in duplicates:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, first_seen_at, last_seen_at, expires_at, ttl_seconds "
                    "FROM resolved_ips "
                    "WHERE target_id = :target_id AND ip_address = :ip_address"
                ),
                {"target_id": target_id, "ip_address": ip_address},
            )
        ).fetchall()
        parsed = [
            (
                str(row[0]),
                _parse_sqlite_dt(row[1]),
                _parse_sqlite_dt(row[2]),
                _parse_sqlite_dt(row[3]),
                int(row[4]),
            )
            for row in rows
        ]
        # Keep the row with the longest retention (tie-break on last_seen_at, then id).
        parsed.sort(key=lambda item: (item[3], item[2], item[0]), reverse=True)
        keep_id = parsed[0][0]
        earliest_first = min(item[1] for item in parsed)
        latest_last = max(item[2] for item in parsed)
        latest_expires = max(item[3] for item in parsed)
        best_ttl = max(item[4] for item in parsed)

        await conn.execute(
            text(
                "UPDATE resolved_ips "
                "SET first_seen_at = :first_seen_at, "
                "last_seen_at = :last_seen_at, "
                "expires_at = :expires_at, "
                "ttl_seconds = :ttl_seconds "
                "WHERE id = :keep_id"
            ),
            {
                "first_seen_at": earliest_first.replace(tzinfo=None).isoformat(sep=" "),
                "last_seen_at": latest_last.replace(tzinfo=None).isoformat(sep=" "),
                "expires_at": latest_expires.replace(tzinfo=None).isoformat(sep=" "),
                "ttl_seconds": best_ttl,
                "keep_id": keep_id,
            },
        )
        await conn.execute(
            text(
                "DELETE FROM resolved_ips "
                "WHERE target_id = :target_id AND ip_address = :ip_address AND id != :keep_id"
            ),
            {"target_id": target_id, "ip_address": ip_address, "keep_id": keep_id},
        )

    await conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_resolved_ips_target_ip "
            "ON resolved_ips (target_id, ip_address)"
        )
    )


async def _migrate_manual_targets_uniqueness(conn: AsyncConnection) -> None:
    """Canonicalize manual target values, merge duplicates, and enforce manual uniqueness."""
    cols = (await conn.execute(text("PRAGMA table_info(targets)"))).fetchall()
    if not cols:
        return

    indexes = (await conn.execute(text("PRAGMA index_list(targets)"))).fetchall()
    if any(str(idx[1]) == "uq_targets_manual_value" for idx in indexes):
        return

    manual_rows = (
        await conn.execute(
            text(
                "SELECT id, value, description, enabled, folder_id, created_at "
                "FROM targets WHERE feed_id IS NULL"
            )
        )
    ).fetchall()

    grouped: dict[str, list[tuple[str, str, str | None, bool, str | None, datetime]]] = {}
    for row in manual_rows:
        target_id = str(row[0])
        raw_value = str(row[1])
        canonical = dedupe_key(raw_value)
        grouped.setdefault(canonical, []).append(
            (
                target_id,
                raw_value,
                str(row[2]) if row[2] is not None else None,
                bool(row[3]),
                str(row[4]) if row[4] is not None else None,
                _parse_sqlite_dt(row[5]),
            )
        )

    for canonical, entries in grouped.items():
        if len(entries) == 1:
            target_id, raw_value, *_ = entries[0]
            if raw_value != canonical:
                await conn.execute(
                    text("UPDATE targets SET value = :value WHERE id = :id"),
                    {"value": canonical, "id": target_id},
                )
            continue

        # Keep the active/richest/oldest manual entry so existing metadata and traffic stay intact.
        entries.sort(
            key=lambda item: (
                not item[3],
                item[4] is None,
                item[2] is None,
                item[5],
                item[0],
            )
        )
        keep_id, keep_raw, keep_desc, keep_enabled, keep_folder, _ = entries[0]
        merged_enabled = keep_enabled or any(item[3] for item in entries[1:])
        merged_desc = keep_desc or next((item[2] for item in entries[1:] if item[2]), None)
        merged_folder = keep_folder or next((item[4] for item in entries[1:] if item[4]), None)
        duplicate_ids = [item[0] for item in entries[1:]]

        # Update kept row's metadata first (value updated after duplicates are removed).
        if (
            merged_enabled != keep_enabled
            or merged_desc != keep_desc
            or merged_folder != keep_folder
        ):
            await conn.execute(
                text(
                    "UPDATE targets SET enabled = :enabled, description = :description, "
                    "folder_id = :folder_id WHERE id = :id"
                ),
                {
                    "enabled": merged_enabled,
                    "description": merged_desc,
                    "folder_id": merged_folder,
                    "id": keep_id,
                },
            )

        # Merge any resolved_ips from duplicate rows into the keeper.
        keep_ips_rows = (
            await conn.execute(
                text(
                    "SELECT id, ip_address, first_seen_at, last_seen_at, expires_at, ttl_seconds "
                    "FROM resolved_ips WHERE target_id = :keep_id"
                ),
                {"keep_id": keep_id},
            )
        ).fetchall()
        keep_ips: dict[str, tuple[str, datetime, datetime, datetime, int]] = {
            str(r[1]): (
                str(r[0]),
                _parse_sqlite_dt(r[2]),
                _parse_sqlite_dt(r[3]),
                _parse_sqlite_dt(r[4]),
                int(r[5]),
            )
            for r in keep_ips_rows
        }

        for dup_id in duplicate_ids:
            dup_ips = (
                await conn.execute(
                    text(
                        "SELECT id, ip_address, cidr, first_seen_at, last_seen_at, expires_at, "
                        "ttl_seconds FROM resolved_ips WHERE target_id = :dup_id"
                    ),
                    {"dup_id": dup_id},
                )
            ).fetchall()
            for row in dup_ips:
                ip_addr = str(row[1])
                dup_first = _parse_sqlite_dt(row[3])
                dup_last = _parse_sqlite_dt(row[4])
                dup_expires = _parse_sqlite_dt(row[5])
                dup_ttl = int(row[6])
                if ip_addr in keep_ips:
                    existing_id, k_first, k_last, k_expires, k_ttl = keep_ips[ip_addr]
                    best_first = min(k_first, dup_first)
                    best_last = max(k_last, dup_last)
                    best_expires = max(k_expires, dup_expires)
                    best_ttl = max(k_ttl, dup_ttl)
                    await conn.execute(
                        text(
                            "UPDATE resolved_ips SET first_seen_at = :first_seen_at, "
                            "last_seen_at = :last_seen_at, expires_at = :expires_at, "
                            "ttl_seconds = :ttl_seconds WHERE id = :id"
                        ),
                        {
                            "first_seen_at": best_first.replace(tzinfo=None).isoformat(sep=" "),
                            "last_seen_at": best_last.replace(tzinfo=None).isoformat(sep=" "),
                            "expires_at": best_expires.replace(tzinfo=None).isoformat(sep=" "),
                            "ttl_seconds": best_ttl,
                            "id": existing_id,
                        },
                    )
                    keep_ips[ip_addr] = (
                        existing_id,
                        best_first,
                        best_last,
                        best_expires,
                        best_ttl,
                    )
                else:
                    await conn.execute(
                        text("UPDATE resolved_ips SET target_id = :keep_id WHERE id = :id"),
                        {"keep_id": keep_id, "id": str(row[0])},
                    )
                    keep_ips[ip_addr] = (
                        str(row[0]),
                        dup_first,
                        dup_last,
                        dup_expires,
                        dup_ttl,
                    )

            await conn.execute(text("DELETE FROM targets WHERE id = :id"), {"id": dup_id})

        if keep_raw != canonical:
            await conn.execute(
                text("UPDATE targets SET value = :value WHERE id = :id"),
                {"value": canonical, "id": keep_id},
            )

    await conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_targets_manual_value "
            "ON targets (value) WHERE feed_id IS NULL"
        )
    )


async def _apply_lightweight_migrations(conn: AsyncConnection) -> None:
    """Add newly introduced columns to pre-existing SQLite tables.

    ``Base.metadata.create_all`` only creates missing tables, so databases created by
    earlier releases need explicit ``ALTER TABLE`` statements for new columns.
    """
    result = await conn.execute(text("PRAGMA table_info(targets)"))
    target_columns = {row[1] for row in result.fetchall()}
    if target_columns:
        if "folder_id" not in target_columns:
            await conn.execute(
                text(
                    "ALTER TABLE targets ADD COLUMN folder_id VARCHAR(36) "
                    "REFERENCES folders(id) ON DELETE SET NULL"
                )
            )
        for column, kind in {
            "dns_status": "VARCHAR(32)",
            "dns_error": "TEXT",
            "dns_last_checked_at": "DATETIME",
            "dns_last_resolved_at": "DATETIME",
        }.items():
            if column not in target_columns:
                await conn.execute(text(f"ALTER TABLE targets ADD COLUMN {column} {kind}"))

    feed_columns = {
        row[1] for row in (await conn.execute(text("PRAGMA table_info(feeds)"))).fetchall()
    }
    for column, kind in {
        "blocked": "BOOLEAN",
        "source_updated_at": "DATETIME",
        "last_checked_at": "DATETIME",
        "last_error": "TEXT",
    }.items():
        if column not in feed_columns:
            await conn.execute(text(f"ALTER TABLE feeds ADD COLUMN {column} {kind}"))

    await _migrate_resolved_ips_uniqueness(conn)
    await _migrate_manual_targets_uniqueness(conn)


async def init_db() -> None:
    """Initialize database tables, apply schema upgrades and load persisted settings."""
    from app.core.settings_store import load_into_settings
    from app.db.models import Base

    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL;"))
        await conn.execute(text("PRAGMA synchronous=NORMAL;"))
        await conn.run_sync(Base.metadata.create_all)
        await _apply_lightweight_migrations(conn)

    # Apply previously persisted runtime settings over the environment defaults
    await load_into_settings()
