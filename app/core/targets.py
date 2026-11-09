"""Helpers for classifying, normalizing and deduplicating network targets."""

import ipaddress
from datetime import UTC, datetime
from typing import Literal

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
