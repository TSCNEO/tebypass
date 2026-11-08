"""Feed ingestion and parsing service."""

import ipaddress
import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

import httpx

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

    def parse_content(self, text_content: str, feed_format: str = "json") -> list[ParsedTarget]:
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

        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    t = self.parse_entry(item)
                    if t:
                        targets.append(t)
                elif isinstance(item, dict):
                    val = item.get("value") or item.get("target") or item.get("domain") or item.get("ip")
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
    ) -> tuple[list[ParsedTarget], str | None, bool]:
        """Download remote feed and parse entries. Returns (targets, new_etag, modified)."""
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
                targets = self.parse_content(resp.text, feed_format=feed_format)
                return targets, new_etag, True
            except Exception as ex:
                logger.error("Failed to fetch feed '%s': %s", url, ex)
                return [], etag, False
