"""Feed ingestion and parsing service."""

import ipaddress
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from app.core.config import settings

logger = logging.getLogger("tebypass.feeds")


@dataclass
class ParsedTarget:
    """Extracted destination from a feed."""

    value: str
    destination_type: Literal["fqdn", "cidr"]
    description: str = ""


class FeedManager:
    """Manages downloading, parsing, and validating remote target feeds."""

    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self.timeout = timeout_seconds

    def parse_entry(self, entry: str, description: str = "") -> ParsedTarget | None:
        """Parse and classify a single string entry as CIDR or FQDN."""
        raw = entry.strip()
        if not raw or raw.startswith("#"):
            return None

        # Check if it's an IP address or CIDR
        try:
            # Normalize plain IP to /32 or /128
            if "/" not in raw:
                ip_obj = ipaddress.ip_address(raw)
                cidr_str = f"{ip_obj}/32" if ip_obj.version == 4 else f"{ip_obj}/128"
            else:
                net_obj = ipaddress.ip_network(raw, strict=False)
                cidr_str = str(net_obj)

            return ParsedTarget(
                value=cidr_str,
                destination_type="cidr",
                description=description,
            )
        except ValueError:
            # Not an IP/CIDR; validate as FQDN
            cleaned = raw.lower().rstrip(".")
            if "." in cleaned and " " not in cleaned and not cleaned.startswith("http"):
                return ParsedTarget(
                    value=cleaned,
                    destination_type="fqdn",
                    description=description,
                )

        return None

    @staticmethod
    def _parse_timestamp(raw: Any) -> datetime | None:
        """Parse the timestamp formats used by community feeds (ISO-8601, optional 'Z')."""
        if not isinstance(raw, str):
            return None
        candidate = raw.strip().replace(" ", "T", 1)
        if candidate.endswith(("Z", "z")):
            candidate = f"{candidate[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    @classmethod
    def _latest_state(cls, state_changes: Any) -> bool:
        """Return the most recent state flag of a HayAHora entry.

        Entries without any state change are considered active: the feed only lists
        problematic endpoints, so absence of history is not evidence of normal operation.
        """
        if not isinstance(state_changes, list) or not state_changes:
            return True

        latest_ts: datetime | None = None
        latest_state = True
        for change in state_changes:
            if not isinstance(change, dict):
                continue
            state = change.get("state")
            if not isinstance(state, bool):
                continue
            timestamp = cls._parse_timestamp(change.get("timestamp"))
            if timestamp is None:
                continue
            if latest_ts is None or timestamp >= latest_ts:
                latest_ts = timestamp
                latest_state = state
        return latest_state

    def _parse_hayahora_entry(
        self,
        entry: Any,
        force_active: bool,
        isp_filter: str,
    ) -> ParsedTarget | None:
        """Parse a single ``data[]`` element of the HayAHora / StopLiga payload."""
        if not isinstance(entry, dict):
            return None

        ip_value = entry.get("ip")
        if not isinstance(ip_value, str) or not ip_value.strip():
            return None

        isp = entry.get("isp")
        isp_name = isp.strip() if isinstance(isp, str) else ""

        if isp_filter and isp_name.lower() != isp_filter.lower():
            return None

        if not force_active and not self._latest_state(entry.get("stateChanges")):
            return None

        label = entry.get("description")
        label_text = label.strip() if isinstance(label, str) else ""
        description = " · ".join(part for part in (label_text, isp_name) if part)

        return self.parse_entry(ip_value, description=description)

    def parse_hayahora(
        self,
        payload: Any,
        force_active: bool = False,
        isp_filter: str = "",
        max_targets: int = 200,
    ) -> list[ParsedTarget]:
        """Parse the ``{"lastUpdate": ..., "data": [...]}`` HayAHora document."""
        if not isinstance(payload, dict):
            return []

        entries = payload.get("data")
        if not isinstance(entries, list):
            return []

        filter_value = isp_filter.strip()
        targets: list[ParsedTarget] = []
        seen: set[str] = set()

        for entry in entries:
            target = self._parse_hayahora_entry(entry, force_active, filter_value)
            if target is None or target.value in seen:
                continue
            seen.add(target.value)
            targets.append(target)
            if len(targets) >= max_targets:
                logger.warning(
                    "Feed safety ceiling (%d targets) reached; truncating import to prevent router saturation",
                    max_targets,
                )
                break

        return targets

    def parse_content(
        self,
        text_content: str,
        feed_format: str = "json",
        force_active: bool = True,
        isp_filter: str = "",
    ) -> list[ParsedTarget]:
        """Parse raw response text into a list of valid ParsedTargets."""
        fmt = feed_format.lower().strip()
        targets: list[ParsedTarget] = []

        if fmt == "plaintext":
            for line in text_content.splitlines():
                target = self.parse_entry(line)
                if target:
                    targets.append(target)
            return targets

        # Try JSON parsing
        try:
            data: Any = json.loads(text_content)
        except json.JSONDecodeError:
            # Fallback to line-by-line if JSON fails
            return self.parse_content(text_content, feed_format="plaintext")

        # HayAHora / StopLiga live document (format flag or structural auto-detection)
        is_hayahora = fmt == "hayahora" or (
            isinstance(data, dict) and isinstance(data.get("data"), list)
        )
        if is_hayahora:
            return self.parse_hayahora(data, force_active=force_active, isp_filter=isp_filter)

        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    t = self.parse_entry(item)
                    if t:
                        targets.append(t)
                elif isinstance(item, dict):
                    val = (
                        item.get("value")
                        or item.get("target")
                        or item.get("domain")
                        or item.get("ip")
                    )
                    desc = item.get("description", "")
                    if val and isinstance(val, str):
                        t = self.parse_entry(val, description=desc)
                        if t:
                            targets.append(t)
        elif isinstance(data, dict):
            # StopLiga / HayAHora community style dictionary {domains: [], ips: []}
            domains = data.get("domains") or data.get("hosts") or []
            ips = data.get("ips") or data.get("cidrs") or []

            if isinstance(domains, list):
                for d in domains:
                    if isinstance(d, str):
                        t = self.parse_entry(d)
                        if t:
                            targets.append(t)

            if isinstance(ips, list):
                for ip in ips:
                    if isinstance(ip, str):
                        t = self.parse_entry(ip)
                        if t:
                            targets.append(t)

        return targets

    async def fetch_feed(
        self,
        url: str,
        feed_format: str = "json",
        etag: str | None = None,
        force_active: bool | None = None,
        isp_filter: str | None = None,
    ) -> tuple[list[ParsedTarget], str | None, bool]:
        """Download a remote feed and parse entries. Returns (targets, new_etag, modified)."""
        effective_force_active = (
            settings.feed_force_active if force_active is None else force_active
        )
        effective_isp_filter = settings.feed_isp_filter if isp_filter is None else isp_filter

        headers: dict[str, str] = {
            "User-Agent": "TeByPass/0.1.0 (+https://github.com/TSCNEO/tebypass)",
        }
        if etag:
            headers["If-None-Match"] = etag

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 304:
                    return [], etag, False

                resp.raise_for_status()
                new_etag = resp.headers.get("etag")
                targets = self.parse_content(
                    resp.text,
                    feed_format=feed_format,
                    force_active=effective_force_active,
                    isp_filter=effective_isp_filter,
                )
                return targets, new_etag, True
            except Exception as ex:
                logger.error("Failed to fetch feed '%s': %s", url, ex)
                return [], etag, False
