"""In-memory sliding-window TTL cache for resolved target IPs."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass
class CachedResolution:
    """Entry holding resolved IP metadata and dynamic sliding expiration."""

    fqdn: str
    ip_address: str
    cidr: str
    first_seen_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    dns_ttl: int

    def is_expired(self, now: datetime | None = None) -> bool:
        """Check if entry has passed its extended expiration."""
        current_time = now or datetime.now(UTC)
        return current_time > self.expires_at


class SlidingWindowCache:
    """Thread-safe cache that extends IP lifetime dynamically when active."""

    def __init__(self, default_window_seconds: int = 3600) -> None:
        self.default_window_seconds = default_window_seconds
        self._entries: dict[str, CachedResolution] = {}  # key: (fqdn, ip)
        self._lock = asyncio.Lock()

    def _make_key(self, fqdn: str, ip_address: str) -> str:
        return f"{fqdn.lower().strip()}:{ip_address.strip()}"

    async def add_or_extend(
        self,
        fqdn: str,
        ip_address: str,
        cidr: str,
        dns_ttl: int,
        window_extension_seconds: int | None = None,
    ) -> CachedResolution:
        """Add a resolved IP or slide forward its expiration if already known."""
        async with self._lock:
            now = datetime.now(UTC)
            extension = window_extension_seconds or self.default_window_seconds
            key = self._make_key(fqdn, ip_address)

            # Slide expiration: max(now + dns_ttl, now + window_extension)
            extended_ttl = max(dns_ttl, extension)
            expires_at = now + timedelta(seconds=extended_ttl)

            if key in self._entries:
                entry = self._entries[key]
                entry.last_seen_at = now
                # Never shorten an existing longer expiration
                if expires_at > entry.expires_at:
                    entry.expires_at = expires_at
                entry.dns_ttl = dns_ttl
                return entry
            else:
                entry = CachedResolution(
                    fqdn=fqdn.lower().strip(),
                    ip_address=ip_address.strip(),
                    cidr=cidr.strip(),
                    first_seen_at=now,
                    last_seen_at=now,
                    expires_at=expires_at,
                    dns_ttl=dns_ttl,
                )
                self._entries[key] = entry
                return entry

    async def get_active_cidrs(self, now: datetime | None = None) -> set[str]:
        """Return the set of all unexpired CIDRs currently in cache."""
        async with self._lock:
            current_time = now or datetime.now(UTC)
            active: set[str] = set()
            for entry in self._entries.values():
                if not entry.is_expired(current_time):
                    active.add(entry.cidr)
            return active

    async def purge_expired(self, now: datetime | None = None) -> int:
        """Remove entries that have fully expired beyond the sliding window."""
        async with self._lock:
            current_time = now or datetime.now(UTC)
            keys_to_remove = [k for k, v in self._entries.items() if v.is_expired(current_time)]
            for k in keys_to_remove:
                del self._entries[k]
            return len(keys_to_remove)

    async def get_entries_for_fqdn(self, fqdn: str) -> list[CachedResolution]:
        """Get all active cached entries for a given domain."""
        async with self._lock:
            norm_fqdn = fqdn.lower().strip()
            now = datetime.now(UTC)
            return [
                v for v in self._entries.values() if v.fqdn == norm_fqdn and not v.is_expired(now)
            ]

    async def size(self) -> int:
        """Return count of cached resolutions."""
        async with self._lock:
            return len(self._entries)

    async def clear(self) -> None:
        """Flush all cache entries."""
        async with self._lock:
            self._entries.clear()
