"""Persisted ownership of the remote routing policy managed by the active driver.

Configuration identity and remote ownership are separate concerns. A driver owns a
policy only after a write has been attempted under a given configuration identity. The
record lets a settings update answer three different cases without guessing from the last
sync result:

* The current configuration owns a policy: retire it before persisting a new identity.
* The current configuration never wrote: allow correcting or replacing it.
* Ownership is unknown: require an explicit acknowledgement before abandoning it.

A failed cycle never clears an existing record. Only a verified teardown/empty policy, or
an explicit acknowledgement, does.
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.config import Settings
from app.core.driver_configuration import driver_identity
from app.db.models import RuntimeState, SyncEvent
from app.db.session import AsyncSessionLocal

logger = logging.getLogger("tebypass.ownership")

OWNER_KEY = "router_policy_owner"
REMOTE_STATE_KEY = "router_policy_remote_state"
TRACKING_KEY = "router_policy_ownership_known"

# Drivers that mutate a remote device. Mock only simulates in memory.
REMOTE_DRIVERS = frozenset({"unifi", "mikrotik", "omada", "opnsense", "virtual_gateway"})


@dataclass(frozen=True)
class PolicyOwner:
    """Configuration identity that may have written the remote policy."""

    identity: str
    driver: str
    recorded_at: str
    possible_partial: bool = False


def owns_remote_state(driver_name: str) -> bool:
    """Return True when the driver can manage state outside this process."""
    return driver_name in REMOTE_DRIVERS


async def _mark_tracked_in_session(session: Any) -> None:
    row = await session.get(RuntimeState, TRACKING_KEY)
    if row is None:
        session.add(RuntimeState(key=TRACKING_KEY, value="1"))


async def is_ownership_tracked() -> bool:
    """Return True once ownership tracking has governed at least one cycle or transition."""
    async with AsyncSessionLocal() as session:
        return await session.get(RuntimeState, TRACKING_KEY) is not None


async def mark_ownership_tracked() -> None:
    """Mark that the installation has transitioned to explicit ownership tracking."""
    async with AsyncSessionLocal() as session:
        await _mark_tracked_in_session(session)
        await session.commit()


async def read_policy_owner() -> PolicyOwner | None:
    """Read the persisted owner record, tolerating a corrupt/legacy value."""
    async with AsyncSessionLocal() as session:
        row = await session.get(RuntimeState, OWNER_KEY)
        if row is None:
            return None
        try:
            data = json.loads(row.value)
        except (TypeError, json.JSONDecodeError):
            logger.warning("Ignoring unreadable router policy owner record")
            return None
        if not isinstance(data, dict) or not isinstance(data.get("identity"), str):
            return None
        return PolicyOwner(
            identity=data["identity"],
            driver=str(data.get("driver", "")),
            recorded_at=str(data.get("recorded_at", "")),
            possible_partial=bool(data.get("possible_partial", False)),
        )


async def record_policy_owner(cfg: Settings, *, possible_partial: bool = False) -> None:
    """Remember that the current configuration may own a remote policy."""
    record = {
        "identity": driver_identity(cfg),
        "driver": cfg.router_driver,
        "recorded_at": datetime.now(UTC).isoformat(),
        "possible_partial": possible_partial,
    }
    async with AsyncSessionLocal() as session:
        row = await session.get(RuntimeState, OWNER_KEY)
        value = json.dumps(record, sort_keys=True)
        if row is None:
            session.add(RuntimeState(key=OWNER_KEY, value=value))
        else:
            row.value = value
        await _mark_tracked_in_session(session)
        await session.commit()


async def clear_policy_owner() -> None:
    """Forget the owner after a verified teardown or a verified empty policy."""
    async with AsyncSessionLocal() as session:
        row = await session.get(RuntimeState, OWNER_KEY)
        if row is not None:
            await session.delete(row)
        await _mark_tracked_in_session(session)
        await session.commit()


async def latest_applied_policy(driver_name: str) -> bool:
    """Detect a policy applied before ownership tracking existed (upgrade path).

    The latest successful event is authoritative for the last known remote state. A
    later failed event does not erase that evidence.
    """
    async with AsyncSessionLocal() as session:
        latest = (
            await session.execute(
                select(SyncEvent)
                .where(SyncEvent.driver_name == driver_name, SyncEvent.success.is_(True))
                .order_by(SyncEvent.executed_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    return latest is not None and latest.total_active > 0


async def record_unverified_remote_state(driver_name: str, detail: str) -> None:
    """Record that a policy may remain after an explicitly acknowledged teardown failure."""
    record: dict[str, Any] = {
        "state": "unverified",
        "driver": driver_name,
        "detail": detail,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    async with AsyncSessionLocal() as session:
        row = await session.get(RuntimeState, REMOTE_STATE_KEY)
        value = json.dumps(record, sort_keys=True)
        if row is None:
            session.add(RuntimeState(key=REMOTE_STATE_KEY, value=value))
        else:
            row.value = value
        await _mark_tracked_in_session(session)
        await session.commit()


async def clear_unverified_remote_state() -> None:
    """Clear the warning after a verified policy application or teardown."""
    async with AsyncSessionLocal() as session:
        row = await session.get(RuntimeState, REMOTE_STATE_KEY)
        if row is not None:
            await session.delete(row)
            await session.commit()


async def read_unverified_remote_state() -> dict[str, Any] | None:
    """Return the acknowledgement record when remote state is still unverified."""
    async with AsyncSessionLocal() as session:
        row = await session.get(RuntimeState, REMOTE_STATE_KEY)
        if row is None:
            return None
        try:
            data = json.loads(row.value)
        except (TypeError, json.JSONDecodeError):
            return None
    if not isinstance(data, dict) or data.get("state") != "unverified":
        return None
    return data
