"""Router driver for the Alpine Linux + nftables virtual gateway sidecar.

Two transports are supported, in this order:

1. **Unix domain socket** (preferred): the sidecar runs ``gateway/controller.py`` and
   exposes ``/data/gateway.sock`` through the shared ``/data`` volume. The application
   never needs ``NET_ADMIN`` and the sidecar owns every nftables mutation.
2. **Local nft binary**: when the application itself runs with ``NET_ADMIN`` on the
   same network namespace as the ruleset.
3. **Emulated**: in-memory cache, used by tests and dry runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import shutil
import time
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings
from app.drivers.base import (
    BaseRouterDriver,
    DriverHealth,
    DriverStatus,
    SyncResult,
    TargetCIDR,
)

logger = logging.getLogger(__name__)

DEFAULT_GATEWAY_SOCKET = "/data/gateway.sock"
SOCKET_TIMEOUT_SECONDS = 5.0


def canonical_cidr(element: str) -> str:
    """Return the CIDR form of a set element (nft drops the /32 of host addresses)."""
    text = element.strip()
    if not text:
        return text
    candidate = text if "/" in text else f"{text}/32"
    try:
        return str(ipaddress.ip_network(candidate, strict=False))
    except ValueError:
        return text


class VirtualGatewayDriver(BaseRouterDriver):
    """Driver managing dynamic nftables sets on an Alpine Linux sidecar or local host."""

    driver_name: str = "virtual_gateway"

    def __init__(self, set_name: str | None = None, socket_path: str | None = None) -> None:
        self.set_name = set_name or settings.gateway_nft_set_name
        self._socket_path = socket_path
        self._last_sync: datetime | None = None
        self._cached_routes: set[str] = set()
        self._nft_path: str | None = None

    async def initialize(self) -> None:
        """Resolve the control socket or the local nft binary."""
        self._nft_path = shutil.which("nft")

    # ------------------------------------------------------------------ #
    # Transport helpers
    # ------------------------------------------------------------------ #

    def socket_path(self) -> str | None:
        """Return the control socket path when it exists and is a socket."""
        candidates = [
            self._socket_path,
            settings.gateway_control_socket,
            os.environ.get("TEBYPASS_GATEWAY_SOCKET"),
            DEFAULT_GATEWAY_SOCKET,
        ]
        for candidate in candidates:
            if candidate and os.path.exists(candidate):
                return candidate
        return None

    def _nft_binary(self) -> str | None:
        """Resolve the nft binary lazily so a hot-reloaded driver is still usable."""
        if self._nft_path is None:
            self._nft_path = shutil.which("nft")
        return self._nft_path

    async def _socket_request(
        self,
        payload: dict[str, Any],
        timeout: float = SOCKET_TIMEOUT_SECONDS,
    ) -> dict[str, Any] | None:
        """Send one JSON-line command to the sidecar; ``None`` when unreachable."""
        path = self.socket_path()
        if path is None:
            return None

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(path),
                timeout=timeout,
            )
        except (OSError, TimeoutError):
            return None

        try:
            writer.write(json.dumps(payload).encode("utf-8") + b"\n")
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        except (OSError, TimeoutError):
            return None
        finally:
            writer.close()
            with contextlib.suppress(OSError, TimeoutError):
                await writer.wait_closed()

        if not line:
            return None
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, dict) else None

    # ------------------------------------------------------------------ #
    # Route operations
    # ------------------------------------------------------------------ #

    async def get_current_routes(self, force: bool = False) -> set[str]:
        """Query the active elements, preferring the control socket."""
        if not force and self._cached_routes:
            return set(self._cached_routes)
        response = await self._socket_request({"cmd": "list"})
        if response is not None and not response.get("ok"):
            raise RuntimeError(str(response.get("error") or "Gateway list failed"))
        if response is not None and response.get("ok"):
            elements = {canonical_cidr(str(item)) for item in response.get("elements", [])}
            self._cached_routes = elements
            return set(elements)

        nft_path = self._nft_binary()
        if not nft_path:
            raise RuntimeError("Gateway unavailable: cannot observe routes")

        try:
            cmd = [nft_path, "-j", "list", "set", "inet", "tebypass", self.set_name]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                elements = self._parse_nft_json(stdout.decode())
                self._cached_routes = elements
                return set(elements)
        except Exception as ex:
            raise RuntimeError("Cannot observe local gateway routes") from ex

        raise RuntimeError("Cannot observe local gateway routes")

    @staticmethod
    def _parse_nft_json(payload: str) -> set[str]:
        """Extract set elements from ``nft -j`` output."""
        elements: set[str] = set()
        try:
            document = json.loads(payload)
        except json.JSONDecodeError:
            return elements
        for item in document.get("nftables", []):
            if not isinstance(item, dict):
                continue
            entry = item.get("set")
            if isinstance(entry, dict) and isinstance(entry.get("elem"), list):
                elements.update(canonical_cidr(str(element)) for element in entry["elem"])
        return elements

    async def sync_routes(
        self,
        to_add: set[TargetCIDR],
        to_remove: set[str],
    ) -> SyncResult:
        """Apply incremental route changes through the socket, nft, or the emulator."""
        start = time.perf_counter()
        add_cidrs = sorted({item.cidr for item in to_add})
        remove_cidrs = sorted(to_remove)

        response = await self._socket_request(
            {"cmd": "sync", "add": add_cidrs, "remove": remove_cidrs},
            timeout=SOCKET_TIMEOUT_SECONDS * 2,
        )
        if response is not None:
            if not response.get("ok"):
                return self._failure(start, str(response.get("error") or "gateway sync failed"))
            returned = response.get("elements")
            if isinstance(returned, list):
                self._cached_routes = {canonical_cidr(str(item)) for item in returned}
            else:
                self._cached_routes = (self._cached_routes - set(remove_cidrs)) | set(add_cidrs)
            self._last_sync = datetime.now(UTC)
            return SyncResult(
                driver_name=self.driver_name,
                added_count=int(response.get("added_count", 0)),
                removed_count=int(response.get("removed_count", 0)),
                total_active=int(response.get("total_active", len(self._cached_routes))),
                duration_ms=float(
                    response.get("duration_ms", round((time.perf_counter() - start) * 1000.0, 2))
                ),
                success=True,
                error_message=None,
            )

        nft_path = self._nft_binary()
        if nft_path:
            commands: list[str] = []
            for cidr in add_cidrs:
                commands.append(f"add element inet tebypass {self.set_name} {{ {cidr} }}")
            for cidr in remove_cidrs:
                commands.append(f"delete element inet tebypass {self.set_name} {{ {cidr} }}")

            if commands:
                script = "\n".join(commands) + "\n"
                try:
                    proc = await asyncio.create_subprocess_exec(
                        nft_path,
                        "-f",
                        "-",
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, stderr = await proc.communicate(input=script.encode())
                    if proc.returncode != 0:
                        return self._failure(start, stderr.decode().strip())
                except Exception as ex:
                    return self._failure(start, str(ex))

                self._cached_routes = (self._cached_routes - set(remove_cidrs)) | set(add_cidrs)
                self._last_sync = datetime.now(UTC)
                return SyncResult(
                    driver_name=self.driver_name,
                    added_count=len(add_cidrs),
                    removed_count=len(remove_cidrs),
                    total_active=len(self._cached_routes),
                    duration_ms=round((time.perf_counter() - start) * 1000.0, 2),
                    success=True,
                    error_message=None,
                )

        return self._failure(start, "Gateway unavailable: no control socket or local nft transport")

    def _failure(self, start: float, message: str) -> SyncResult:
        """Build a failed SyncResult without mutating the cached state."""
        return SyncResult(
            driver_name=self.driver_name,
            added_count=0,
            removed_count=0,
            total_active=len(self._cached_routes),
            duration_ms=round((time.perf_counter() - start) * 1000.0, 2),
            success=False,
            error_message=message or "unknown error",
        )

    # ------------------------------------------------------------------ #
    # Health
    # ------------------------------------------------------------------ #

    async def health_check(self) -> DriverStatus:
        """Inspect the sidecar through the socket, falling back to local nftables."""
        response = await self._socket_request({"cmd": "status"})
        if response is not None:
            return self._status_from_socket(response)

        nft_path = self._nft_binary()
        details: dict[str, Any] = {
            "transport": "local_nft" if nft_path else "unavailable",
            "socket_path": self.socket_path() or "none",
            "nft_available": nft_path is not None,
            "set_name": self.set_name,
            "binary_path": nft_path or "none",
        }
        health = DriverHealth.DEGRADED if nft_path else DriverHealth.UNHEALTHY
        return DriverStatus(
            driver_name=self.driver_name,
            health=health,
            active_routes_count=len(self._cached_routes),
            last_sync=self._last_sync,
            details=details,
        )

    def _status_from_socket(self, response: dict[str, Any]) -> DriverStatus:
        wireguard = response.get("wireguard") or {}
        nftables = response.get("nftables") or {}
        elements = response.get("elements")
        if isinstance(elements, list):
            self._cached_routes = {canonical_cidr(str(item)) for item in elements}

        socket_ok = bool(response.get("ok"))
        set_ok = bool(nftables.get("set"))
        wg_present = bool(wireguard.get("present"))
        if socket_ok:
            health = DriverHealth.HEALTHY
        elif set_ok or wg_present:
            health = DriverHealth.DEGRADED
        else:
            health = DriverHealth.UNHEALTHY

        details: dict[str, Any] = {
            "transport": "unix_socket",
            "socket_path": self.socket_path() or "none",
            "set_name": nftables.get("set_name", self.set_name),
            "wireguard_interface": response.get("interface", "wg0"),
            "wireguard_up": wg_present,
            "wireguard_handshake_age": wireguard.get("handshake_age"),
            "nft_table": bool(nftables.get("table")),
            "nft_set": set_ok,
            "mark_rule": bool(nftables.get("mark_rule")),
            "error": response.get("error"),
        }
        return DriverStatus(
            driver_name=self.driver_name,
            health=health,
            active_routes_count=len(self._cached_routes),
            last_sync=self._last_sync,
            details=details,
        )

    async def check_health(self) -> DriverStatus:
        """Alias of :meth:`health_check` kept for callers of the gateway contract."""
        return await self.health_check()

    async def close(self) -> None:
        """Release driver resources (no persistent connection is held)."""
        return None

    async def teardown(self) -> None:
        """Flush the target set and verify the effective remote state."""
        response = await self._socket_request({"cmd": "flush"})
        if response is not None:
            if not response.get("ok"):
                raise RuntimeError(str(response.get("error") or "Gateway flush failed"))
            verified = await self._socket_request({"cmd": "list"})
            if verified is None or not verified.get("ok") or verified.get("elements") != []:
                raise RuntimeError("Gateway flush could not be verified")
        else:
            nft = self._nft_binary()
            if not nft:
                raise RuntimeError("Gateway unavailable during teardown")
            proc = await asyncio.create_subprocess_exec(
                nft,
                "flush",
                "set",
                "inet",
                "tebypass",
                self.set_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(stderr.decode().strip() or "nft flush failed")
            if await self.get_current_routes(force=True):
                raise RuntimeError("Local gateway flush could not be verified")
        self._cached_routes.clear()
