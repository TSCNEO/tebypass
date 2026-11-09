"""Canonical community snapshot, using stopliga's pinned status semantics."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from stopliga.feed import (
    extract_hayahora_active_ips,
    parse_status_payload_value,
    validate_feed_freshness,
)

from app.core.config import settings


@dataclass(frozen=True)
class CommunitySnapshot:
    blocked: bool
    destinations: list[str]
    updated_at: datetime


def parse_snapshot(payload: Any, force_active: bool = False, isp: str = "") -> CommunitySnapshot:
    """Keep the global signal independent of the selected ISP and destinations."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("Community feed requires a data list")
    validate_feed_freshness(payload, settings.feed_max_age_seconds)
    _, blocked = parse_status_payload_value(payload)
    destinations, _, _ = extract_hayahora_active_ips(
        payload,
        isp=isp or None,
        lookback_hours=settings.feed_lookback_hours,
        max_destinations=settings.feed_max_targets,
    )
    stamp = datetime.fromisoformat(str(payload["lastUpdate"]).replace("Z", "+00:00"))
    return CommunitySnapshot(bool(blocked or force_active), destinations, stamp)
