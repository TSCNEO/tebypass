"""Native asynchronous DNS resolver delegating to host system resolver."""

import asyncio
import ipaddress
import logging
import socket

from app.core.cache import CachedResolution, SlidingWindowCache
from app.core.config import settings

logger = logging.getLogger("tebypass.resolver")


class NativeDNSResolver:
    """Async DNS resolver that respects the local host network and libc getaddrinfo."""

    def __init__(self, cache: SlidingWindowCache, default_dns_ttl: int = 60) -> None:
        self.cache = cache
        self.default_dns_ttl = default_dns_ttl

    async def resolve_fqdn(
        self,
        fqdn: str,
        window_extension_seconds: int | None = None,
    ) -> list[CachedResolution]:
        """Resolve an FQDN to IP addresses using the host's native resolver."""
        clean_fqdn = fqdn.strip().lower()
        loop = asyncio.get_running_loop()
        family = socket.AF_UNSPEC if settings.resolve_ipv6 else socket.AF_INET

        try:
            # Delegate to system getaddrinfo via default executor (non-blocking)
            addr_info = await asyncio.wait_for(
                loop.getaddrinfo(clean_fqdn, None, family=family, type=socket.SOCK_STREAM),
                timeout=5,
            )
        except socket.gaierror as ex:
            logger.warning("DNS resolution failed for '%s': %s", clean_fqdn, ex)
            return []
        except Exception as ex:
            logger.error("Unexpected error resolving '%s': %s", clean_fqdn, ex)
            return []

        resolved_ips: set[str] = set()
        for entry in addr_info:
            sockaddr = entry[4]
            ip_val = sockaddr[0]
            if isinstance(ip_val, str):
                resolved_ips.add(ip_val)

        results: list[CachedResolution] = []
        for ip_str in resolved_ips:
            try:
                ip_obj = ipaddress.ip_address(ip_str)
                if not settings.resolve_ipv6 and ip_obj.version == 6:
                    continue
                cidr = f"{ip_str}/32" if ip_obj.version == 4 else f"{ip_str}/128"

                cached = await self.cache.add_or_extend(
                    fqdn=clean_fqdn,
                    ip_address=ip_str,
                    cidr=cidr,
                    dns_ttl=self.default_dns_ttl,
                    window_extension_seconds=window_extension_seconds,
                )
                results.append(cached)
            except ValueError:
                continue

        return results
