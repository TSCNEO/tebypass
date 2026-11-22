"""Canonical community snapshot, using stopliga's pinned status semantics."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from stopliga.feed import (
    InvalidFeedError,
    compact_history,
    extract_hayahora_active_ips,
    normalize_hayahora_isp,
    parse_status_payload_value,
    validate_feed_freshness,
)

from app.core.config import settings


def parse_community_document(raw_text: str) -> dict[str, Any]:
    """Parse a community history feed via streaming ijson, retaining only latest events."""
    parsed = compact_history(raw_text)
    if not isinstance(parsed, dict):
        raise ValueError("Community feed must decode to a JSON object")
    return {str(key): value for key, value in parsed.items()}


class InvalidISPFilterError(ValueError):
    """Raised when the configured ISP filter does not match any ISP in the community feed."""

    def __init__(self, isp: str, available_isps: list[str]) -> None:
        self.isp = isp
        self.available_isps = available_isps
        if available_isps:
            options = ", ".join(available_isps)
            message = (
                f"El filtro de operador '{isp}' no existe en el feed. "
                f"Operadores disponibles: {options}"
            )
        else:
            message = (
                f"El filtro de operador '{isp}' no coincide con ningún operador en el feed actual."
            )
        super().__init__(message)


def available_community_isps(payload: Any) -> list[str]:
    """Return sorted unique ISP names present in a community status payload."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return []
    found: dict[str, str] = {}
    for entry in payload["data"]:
        if not isinstance(entry, dict):
            continue
        raw_isp = entry.get("isp")
        normalized = normalize_hayahora_isp(raw_isp)
        if normalized is not None and isinstance(raw_isp, str):
            found.setdefault(normalized, raw_isp.strip())
    return sorted(found.values(), key=str.casefold)


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
    try:
        destinations, _, _ = extract_hayahora_active_ips(
            payload,
            isp=isp or None,
            lookback_hours=settings.feed_lookback_hours,
            max_destinations=settings.feed_max_targets,
        )
    except InvalidFeedError as ex:
        if isp and "Unknown Hayahora ISP" in str(ex):
            raise InvalidISPFilterError(isp.strip(), available_community_isps(payload)) from ex
        raise
    stamp = datetime.fromisoformat(str(payload["lastUpdate"]).replace("Z", "+00:00"))
    return CommunitySnapshot(bool(blocked or force_active), destinations, stamp)
