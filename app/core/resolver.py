"""Native asynchronous DNS resolver delegating to host system resolver."""

import asyncio
import ipaddress
import logging
import socket
from dataclasses import dataclass

from app.core.cache import CachedResolution, SlidingWindowCache
from app.core.config import settings

logger = logging.getLogger("tebypass.resolver")


@dataclass(frozen=True)
class DNSLookupResult:
    """Outcome of a single FQDN resolution attempt."""

    resolutions: list[CachedResolution]
    status: str  # "resolved" | "nxdomain" | "timeout" | "empty" | "error"
    error: str | None = None


def classify_dns_exception(ex: BaseException) -> tuple[str, str]:
    """Map a DNS exception to a stable status code and sanitized message."""
    if isinstance(ex, TimeoutError | asyncio.TimeoutError):
        return "timeout", "DNS lookup timed out"
    if isinstance(ex, socket.gaierror):
        again_codes = {code for code in (getattr(socket, "EAI_AGAIN", None),) if code is not None}
        nx_codes = {
            code
            for code in (
                getattr(socket, "EAI_NONAME", None),
                getattr(socket, "EAI_NODATA", None),
            )
            if code is not None
        }
        msg_lower = str(ex).lower()
        if ex.errno in again_codes or "try again" in msg_lower or "timed out" in msg_lower:
            return "timeout", "Temporary DNS resolution failure (EAI_AGAIN)"
        if (
            ex.errno in nx_codes
            or "not known" in msg_lower
            or "nodename" in msg_lower
            or "no address" in msg_lower
        ):
            return "nxdomain", "Domain not found or has no DNS records (NXDOMAIN)"
        return "error", f"DNS lookup failed ({ex.strerror or type(ex).__name__})"
    return "error", f"DNS lookup error ({type(ex).__name__})"


class NativeDNSResolver:
    """Async DNS resolver that respects the local host network and libc getaddrinfo."""

    def __init__(self, cache: SlidingWindowCache, default_dns_ttl: int = 60) -> None:
        self.cache = cache
        self.default_dns_ttl = default_dns_ttl

    async def resolve_fqdn_with_status(
        self,
        fqdn: str,
        window_extension_seconds: int | None = None,
    ) -> DNSLookupResult:
        """Resolve an FQDN and return both resolutions and a classified status."""
        if "resolve_fqdn" in self.__dict__ or type(self).resolve_fqdn is not _ORIGINAL_RESOLVE_FQDN:
            try:
                resolutions = await self.resolve_fqdn(
                    fqdn=fqdn,
                    window_extension_seconds=window_extension_seconds,
                )
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                status, detail = classify_dns_exception(ex)
                logger.warning("DNS resolution failed for '%s' (%s): %s", fqdn, status, ex)
                return DNSLookupResult(resolutions=[], status=status, error=detail)
            if resolutions:
                return DNSLookupResult(resolutions=resolutions, status="resolved", error=None)
            return DNSLookupResult(
                resolutions=[],
                status="empty",
                error="DNS returned no usable IP addresses",
            )

        clean_fqdn = fqdn.strip().lower()
        loop = asyncio.get_running_loop()
        family = socket.AF_UNSPEC if settings.resolve_ipv6 else socket.AF_INET

        try:
            # Delegate to system getaddrinfo via default executor (non-blocking)
            addr_info = await asyncio.wait_for(
                loop.getaddrinfo(clean_fqdn, None, family=family, type=socket.SOCK_STREAM),
                timeout=5,
            )
        except Exception as ex:
            status, detail = classify_dns_exception(ex)
            logger.warning("DNS resolution failed for '%s' (%s): %s", clean_fqdn, status, ex)
            return DNSLookupResult(resolutions=[], status=status, error=detail)

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

        if not results:
            return DNSLookupResult(
                resolutions=[],
                status="empty",
                error="DNS returned no usable IP addresses",
            )
        return DNSLookupResult(resolutions=results, status="resolved", error=None)

    async def resolve_fqdn(
        self,
        fqdn: str,
        window_extension_seconds: int | None = None,
    ) -> list[CachedResolution]:
        """Resolve an FQDN to IP addresses using the host's native resolver."""
        outcome = await self.resolve_fqdn_with_status(
            fqdn=fqdn,
            window_extension_seconds=window_extension_seconds,
        )
        return outcome.resolutions


_ORIGINAL_RESOLVE_FQDN = NativeDNSResolver.resolve_fqdn
