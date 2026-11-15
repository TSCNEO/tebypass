"""Helpers for classifying, normalizing and deduplicating network targets."""

import ipaddress
from datetime import UTC, datetime
from typing import Any, Literal

DestinationType = Literal["fqdn", "cidr"]


def detect_destination_type(raw_value: str) -> DestinationType:
    """Classify a raw entry as ``cidr`` (IP or prefix) or ``fqdn``."""
    raw = raw_value.strip()
    try:
        if "/" in raw:
            ipaddress.ip_network(raw, strict=False)
        else:
            ipaddress.ip_address(raw)
    except ValueError:
        return "fqdn"
    return "cidr"


def dedupe_key(raw_value: str) -> str:
    """Build a comparison key so equivalent notations collide (IP vs IP/32)."""
    raw = raw_value.strip()
    try:
        if "/" in raw:
            return str(ipaddress.ip_network(raw, strict=False))
        ip_obj = ipaddress.ip_address(raw)
    except ValueError:
        return raw.lower().rstrip(".")
    return f"{ip_obj}/32" if ip_obj.version == 4 else f"{ip_obj}/128"


def as_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime (SQLite may hand back naive values)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def build_contributing_targets_query() -> Any:
    """Build the canonical query for targets that should contribute routes right now.

    Excludes paused targets, disabled feeds, community feeds with inactive global signal,
    and community feeds outside the configured active schedule window.
    """
    from sqlalchemy import and_, or_, select

    from app.core.schedule import community_schedule
    from app.db.models import Feed, Target

    query = (
        select(Target)
        .join(Feed, Target.feed_id == Feed.id, isouter=True)
        .where(
            Target.enabled.is_(True),
            or_(Target.feed_id.is_(None), Feed.enabled.is_(True)),
            or_(Target.feed_id.is_(None), Feed.blocked.is_(None), Feed.blocked.is_(True)),
        )
    )
    if not community_schedule().is_active():
        query = query.where(
            or_(
                Target.feed_id.is_(None),
                and_(Feed.blocked.is_(None), Feed.format.not_in(["hayahora", "stopliga"])),
            )
        )
    return query
