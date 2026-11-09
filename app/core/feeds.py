"""Pure feed parsing and destination normalization; HTTP lives in feed_http."""

import ipaddress
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from app.core.config import settings


@dataclass(frozen=True)
class ParsedTarget:
    value: str
    destination_type: Literal["fqdn", "cidr"]
    description: str = ""


class FeedManager:
    def parse_entry(self, entry: str, description: str = "") -> ParsedTarget | None:
        raw = entry.strip()
        if not raw or raw.startswith("#"):
            return None
        try:
            network = ipaddress.ip_network(raw, strict=False)
            return ParsedTarget(str(network), "cidr", description)
        except ValueError:
            cleaned = raw.lower().rstrip(".")
            labels = cleaned.split(".")
            if (
                len(cleaned) <= 253
                and len(labels) > 1
                and not labels[-1].isdigit()
                and all(
                    re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels
                )
            ):
                return ParsedTarget(cleaned, "fqdn", description)
        return None

    def parse_content(
        self,
        text_content: str,
        feed_format: str = "json",
        force_active: bool = False,
        isp_filter: str = "",
    ) -> list[ParsedTarget]:
        fmt = feed_format.strip().lower()
        if fmt not in {"json", "plaintext", "hayahora", "stopliga"}:
            raise ValueError("Unsupported feed format")
        data: Any = None
        if fmt != "plaintext":
            try:
                data = json.loads(text_content)
            except json.JSONDecodeError as ex:
                raise ValueError("Invalid JSON feed") from ex
        community = fmt in {"hayahora", "stopliga"} or (isinstance(data, dict) and "data" in data)
        if community:
            from app.core.community import parse_snapshot

            snapshot = parse_snapshot(data, force_active, isp_filter)
            entries: list[Any] = snapshot.destinations if snapshot.blocked else []
        elif fmt == "plaintext":
            entries = [
                line
                for line in text_content.splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
        elif isinstance(data, list):
            entries = data
        elif isinstance(data, dict) and any(
            key in data for key in ("domains", "hosts", "ips", "cidrs")
        ):
            entries = []
            for key in ("domains", "hosts", "ips", "cidrs"):
                value = data.get(key, [])
                if not isinstance(value, list):
                    raise ValueError("Feed destination fields must be lists")
                entries.extend(value)
        else:
            raise ValueError("Unsupported feed document")
        parsed: dict[str, ParsedTarget] = {}
        for item in entries:
            description = ""
            if isinstance(item, dict):
                description = str(item.get("description", ""))[:255]
                item = (
                    item.get("value") or item.get("target") or item.get("domain") or item.get("ip")
                )
            target = self.parse_entry(item, description) if isinstance(item, str) else None
            if target is None:
                raise ValueError("Invalid feed destination")
            parsed[target.value] = target
            if len(parsed) > settings.feed_max_targets:
                raise ValueError("Feed exceeds target safety ceiling")
        return list(parsed.values())
