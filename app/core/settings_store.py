"""SQLite-backed runtime settings store.

All configuration except the bootstrap values (listen host/port and database URL) lives in
the ``app_settings`` table and is editable from the web UI. Environment variables remain a
startup fallback only: values persisted in the database always win.
"""

import logging
from typing import Any, Final

from sqlalchemy import delete, select

from app.core.config import Settings, settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.db.models import AppSetting
from app.db.session import AsyncSessionLocal
from app.drivers import invalidate_driver

logger = logging.getLogger("tebypass.settings")

SECRET_KEYS: Final[set[str]] = {
    "unifi_api_key",
    "unifi_password",
    "mikrotik_password",
}

MASKED_SECRET: Final[str] = "***saved***"

_INT_KEYS: Final[set[str]] = {"sync_interval_seconds", "sliding_window_seconds"}
_BOOL_KEYS: Final[set[str]] = {"feed_force_active", "gateway_fail_closed", "resolve_ipv6"}
# Bootstrap/internal keys: not editable from the UI (changing them at runtime cannot take effect)
_NON_EDITABLE_KEYS: Final[set[str]] = {
    "host",
    "port",
    "database_url",
    "db_path",
    "secret_key",
    "master_pin_hash",
}

_TRUTHY: Final[set[str]] = {"1", "true", "yes", "on"}
_FALSY: Final[set[str]] = {"0", "false", "no", "off", ""}


def _build_editable_keys() -> dict[str, type[Any]]:
    """Derive the editable key whitelist (and its value type) from the Settings model."""
    editable: dict[str, type[Any]] = {}
    for field_name in Settings.model_fields:
        if field_name in _NON_EDITABLE_KEYS:
            continue
        if field_name in _INT_KEYS:
            editable[field_name] = int
        elif field_name in _BOOL_KEYS:
            editable[field_name] = bool
        else:
            editable[field_name] = str
    return editable


EDITABLE_KEYS: Final[dict[str, type[Any]]] = _build_editable_keys()


def _coerce_bool(key: str, raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in _TRUTHY:
        return True
    if text in _FALSY:
        return False
    raise ValueError(f"Setting '{key}' expects a boolean value")


def _coerce_int(key: str, raw: Any) -> int:
    if isinstance(raw, bool):
        raise ValueError(f"Setting '{key}' expects an integer value")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError) as ex:
        raise ValueError(f"Setting '{key}' expects an integer value") from ex
    if value < 0:
        raise ValueError(f"Setting '{key}' must be greater than or equal to 0")
    return value


def _coerce_str(key: str, raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        return str(raw)
    if isinstance(raw, bool):
        return "true" if raw else "false"
    raise ValueError(f"Setting '{key}' expects a string value")


def _coerce(key: str, raw: Any) -> Any:
    """Coerce a raw value to the declared type of an editable setting."""
    kind = EDITABLE_KEYS.get(key)
    if kind is None:
        raise ValueError(f"Setting '{key}' is not editable")
    if kind is int:
        return _coerce_int(key, raw)
    if kind is bool:
        return _coerce_bool(key, raw)
    return _coerce_str(key, raw)


def _serialize(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _is_masked_or_empty(key: str, raw: Any) -> bool:
    """Return True when a value must not overwrite the stored one."""
    if raw is None:
        return True
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return True
        if key in SECRET_KEYS and stripped == MASKED_SECRET:
            return True
    return False


async def load_into_settings() -> None:
    """Read every persisted setting and apply it over the in-memory singleton.

    Secrets stored as legacy plaintext are transparently decrypted and upgraded
    to authenticated ciphertext in SQLite on the fly.
    """
    reencrypt_needed = False
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(AppSetting))).scalars().all()

        applied = 0
        for row in rows:
            if row.value is None:
                continue

            raw_val = row.value
            if row.key in SECRET_KEYS:
                val_to_use, was_encrypted = decrypt_secret(raw_val)
                if not was_encrypted and raw_val:
                    # Auto-upgrade plaintext secret to encrypted in SQLite
                    row.value = encrypt_secret(val_to_use)
                    row.is_secret = True
                    reencrypt_needed = True
            else:
                val_to_use = raw_val

            try:
                value = _coerce(row.key, val_to_use)
            except ValueError as ex:
                logger.warning("Ignoring persisted setting '%s': %s", row.key, ex)
                continue
            setattr(settings, row.key, value)
            applied += 1

        if reencrypt_needed:
            await session.commit()
            logger.info("Upgraded legacy plaintext secrets to encrypted at rest in SQLite")

    invalidate_driver()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.getLogger("app").setLevel(log_level)
    logger.info("Loaded %d persisted settings from SQLite", applied)


async def save_settings(payload: dict[str, Any]) -> None:
    """Validate, persist (encrypting secrets) and hot-apply a batch of settings.

    Unknown keys are ignored, as are ``None``/empty values and the ``***saved***`` mask so a
    masked secret returned by the API never overwrites the real stored secret.
    """
    updates: dict[str, Any] = {}
    for key, raw in payload.items():
        if key not in EDITABLE_KEYS:
            logger.warning("Ignoring unknown setting '%s'", key)
            continue
        if _is_masked_or_empty(key, raw):
            continue
        updates[key] = _coerce(key, raw)

    if not updates:
        return

    async with AsyncSessionLocal() as session:
        for key, value in updates.items():
            existing = await session.get(AppSetting, key)
            serialized = _serialize(value)
            is_secret = key in SECRET_KEYS
            db_value = encrypt_secret(serialized) if is_secret else serialized

            if existing is None:
                session.add(AppSetting(key=key, value=db_value, is_secret=is_secret))
            else:
                existing.value = db_value
                existing.is_secret = is_secret
        await session.commit()

    for key, value in updates.items():
        setattr(settings, key, value)

    invalidate_driver()
    logger.info("Persisted settings: %s", ", ".join(sorted(updates)))


def masked_dump() -> dict[str, Any]:
    """Return all editable settings, masking any stored secret value."""
    dump: dict[str, Any] = {}
    for key in EDITABLE_KEYS:
        value = getattr(settings, key, None)
        if key in SECRET_KEYS:
            dump[key] = MASKED_SECRET if value else ""
        elif value is None:
            dump[key] = ""
        else:
            dump[key] = value
    return dump


async def clear_settings() -> None:
    """Delete every persisted setting (used by tests and factory reset flows)."""
    async with AsyncSessionLocal() as session:
        await session.execute(delete(AppSetting))
        await session.commit()
    invalidate_driver()
