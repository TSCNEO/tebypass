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
from app.core.driver_configuration import (
    IDENTITY_KEYS,
    driver_identity,
    missing_driver_configuration,
)
from app.core.policy_ownership import (
    clear_policy_owner,
    clear_unverified_remote_state,
    is_ownership_tracked,
    latest_applied_policy,
    mark_ownership_tracked,
    read_policy_owner,
    read_unverified_remote_state,
    record_unverified_remote_state,
)
from app.db.models import AppSetting
from app.db.session import AsyncSessionLocal
from app.drivers import invalidate_driver
from app.drivers.capabilities import IPV4_ONLY_DRIVERS, validate_destination

logger = logging.getLogger("tebypass.settings")

SECRET_KEYS: Final[set[str]] = {
    "unifi_api_key",
    "unifi_password",
    "mikrotik_password",
    "omada_client_secret",
    "notification_token",
    "opnsense_api_key",
    "opnsense_api_secret",
}

MASKED_SECRET: Final[str] = "***saved***"

_INT_KEYS: Final[set[str]] = {
    "sync_interval_seconds",
    "sliding_window_seconds",
    "feed_max_age_seconds",
    "feed_lookback_hours",
    "feed_max_targets",
}
_BOOL_KEYS: Final[set[str]] = {
    "feed_force_active",
    "gateway_fail_closed",
    "resolve_ipv6",
    "routing_enabled",
    "unifi_verify_tls",
    "omada_verify_tls",
    "opnsense_verify_tls",
    "mikrotik_verify_tls",
}
# Bootstrap/internal keys: not editable from the UI (changing them at runtime cannot take effect)
_NON_EDITABLE_KEYS: Final[set[str]] = {
    "host",
    "port",
    "database_url",
    "db_path",
    "secret_key",
    "master_pin_hash",
    "gateway_nft_set_name",
    "gateway_control_socket",
    "wireguard_config_path",
    "dns_concurrency",
    "dns_budget_seconds",
    "feed_concurrency",
    "feed_budget_seconds",
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
        if not stripped and key in SECRET_KEYS:
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


async def save_settings(
    payload: dict[str, Any],
    *,
    confirm_remote_state_unknown: bool = False,
) -> None:
    """Apply settings between reconciliation cycles."""
    from app.core.engine import acquire_control_lock

    async with acquire_control_lock():
        await _save_settings(
            payload,
            confirm_remote_state_unknown=confirm_remote_state_unknown,
        )


async def _retire_previous_configuration(*, confirm_remote_state_unknown: bool) -> None:
    """Retire an owned policy before an identity change, or explain why it is safe to skip.

    A candidate that could not have written (missing credentials) or whose ownership was
    never recorded is corrected directly. When ownership exists, retirement must be
    verified: an unreachable router rejects the change unless the caller explicitly
    acknowledges that the previous policy may remain.
    """
    from app.drivers import get_driver

    current_driver = settings.router_driver
    if current_driver == "mock":
        return

    owner = await read_policy_owner()
    current_identity = driver_identity(settings)
    missing = missing_driver_configuration(settings)
    unverified = await read_unverified_remote_state()

    if owner is not None and owner.identity == current_identity:
        reason = "the current configuration owns a remote policy"
    elif owner is not None:
        reason = "a policy was applied under a different configuration identity"
    elif missing:
        logger.info(
            "Skipping teardown for %s: incomplete configuration (%s) cannot have written",
            current_driver,
            ", ".join(missing),
        )
        return
    elif unverified is not None:
        logger.info(
            "Skipping teardown for %s: remote state was already acknowledged as unknown",
            current_driver,
        )
        return
    elif not await is_ownership_tracked() and await latest_applied_policy(current_driver):
        reason = "the last successful sync applied a policy (pre-ownership installation)"
    else:
        logger.info("Skipping teardown for %s: no applied policy recorded", current_driver)
        return

    try:
        await get_driver().teardown()
    except Exception as ex:
        if not confirm_remote_state_unknown:
            raise RuntimeError(
                f"Cannot retire the previous router configuration ({current_driver}): {ex}. "
                "Restore access to the router and retry, or send "
                "confirm_remote_state_unknown=true to accept that the previous policy may remain."
            ) from ex
        logger.warning(
            "Identity change persisted after an unverified teardown (%s, %s): %s",
            current_driver,
            reason,
            ex,
        )
        await record_unverified_remote_state(current_driver, str(ex))
        await clear_policy_owner()
        return

    await clear_policy_owner()
    await clear_unverified_remote_state()


async def _save_settings(
    payload: dict[str, Any],
    *,
    confirm_remote_state_unknown: bool = False,
) -> None:
    """Validate, persist (encrypting secrets) and hot-apply a batch of settings.

    Unknown keys are ignored, as are ``None``/empty values and the ``***saved***`` mask so a
    masked secret returned by the API never overwrites the real stored secret.

    A driver change is only an activation: the candidate must be complete enough to open a
    connection. Completing an already-selected driver is a repair and remains possible even
    while it is still incomplete, so a broken candidate can be fixed incrementally.
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
    candidate = Settings.model_validate({**settings.model_dump(), **updates})
    if (
        candidate.feed_lookback_hours < 1
        or candidate.feed_max_targets < 1
        or candidate.feed_max_age_seconds < 60
    ):
        raise ValueError("Feed lookback/limits must be positive (age >= 60 seconds)")
    if candidate.resolve_ipv6 and candidate.router_driver in IPV4_ONLY_DRIVERS:
        raise ValueError("This driver supports IPv4 only")
    if (
        candidate.router_driver in IPV4_ONLY_DRIVERS
        and candidate.router_driver != settings.router_driver
    ):
        from datetime import UTC, datetime

        from app.db.models import ResolvedIP, Target

        async with AsyncSessionLocal() as session:
            targets = (
                (await session.execute(select(Target).where(Target.enabled.is_(True))))
                .scalars()
                .all()
            )
            for target in targets:
                validate_destination(target.value, target.destination_type, candidate.router_driver)
            retained = (
                (
                    await session.execute(
                        select(ResolvedIP.cidr)
                        .join(Target)
                        .where(Target.enabled.is_(True), ResolvedIP.expires_at > datetime.now(UTC))
                    )
                )
                .scalars()
                .all()
            )
            for cidr in retained:
                validate_destination(cidr, "cidr", candidate.router_driver)
    if candidate.router_driver != settings.router_driver:
        missing = missing_driver_configuration(candidate)
        if missing:
            raise ValueError(f"Driver '{candidate.router_driver}' requires: {', '.join(missing)}")
    from app.core.feed_http import validate_url
    from app.core.schedule import community_schedule

    try:
        community_schedule(candidate)
        validate_url(candidate.hayahora_feed_url)
        if candidate.notification_url:
            validate_url(candidate.notification_url)
            if not candidate.notification_url.startswith("https://"):
                raise ValueError("Webhook must use HTTPS")
    except Exception as ex:
        raise ValueError("Invalid schedule, feed URL or webhook URL") from ex
    from app.drivers import get_driver

    driver_keys = {
        key
        for key in updates
        if key == "router_driver"
        or key.startswith(("unifi_", "mikrotik_", "omada_", "opnsense_", "gateway_"))
    }
    changed_driver = any(getattr(settings, key) != updates[key] for key in driver_keys)
    identity_changed = any(
        key in IDENTITY_KEYS and getattr(settings, key) != value for key, value in updates.items()
    )
    if identity_changed:
        await _retire_previous_configuration(
            confirm_remote_state_unknown=confirm_remote_state_unknown
        )
    if changed_driver:
        await get_driver().close()

    async with AsyncSessionLocal() as session:
        if "hayahora_feed_url" in updates:
            from app.core.feed_http import validate_url
            from app.db.models import Feed

            validate_url(updates["hayahora_feed_url"])
            feeds = (
                (await session.execute(select(Feed).where(Feed.url == settings.hayahora_feed_url)))
                .scalars()
                .all()
            )
            for feed in feeds:
                feed.url = updates["hayahora_feed_url"]
                feed.etag = None
                feed.last_fetched_at = None
        if any(
            k in updates and getattr(settings, k) != updates[k]
            for k in ("feed_isp_filter", "feed_lookback_hours", "feed_force_active")
        ):
            from app.db.models import Feed

            community_feeds = (
                (
                    await session.execute(
                        select(Feed).where(Feed.format.in_(("hayahora", "stopliga")))
                    )
                )
                .scalars()
                .all()
            )
            for feed in community_feeds:
                feed.etag = None
                feed.last_fetched_at = None
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

    if identity_changed:
        await mark_ownership_tracked()

    if changed_driver:
        invalidate_driver()
    from app.core.runtime import engine_instance

    if engine_instance is not None:
        engine_instance.wake()
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
