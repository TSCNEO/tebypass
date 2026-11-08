"""Router driver for Alpine Linux + nftables virtual gateway sidecar."""

import asyncio
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


class VirtualGatewayDriver(BaseRouterDriver):
    """Driver managing dynamic nftables sets on an Alpine Linux sidecar or local host."""

    driver_name: str = "virtual_gateway"

    def __init__(self, set_name: str | None = None) -> None:
        self.set_name = set_name or settings.gateway_nft_set_name
        self._last_sync: datetime | None = None
        self._cached_routes: set[str] = set()
        self._nft_path: str | None = None

    async def initialize(self) -> None:
        """Verify presence of nft binary or socket communication."""
        self._nft_path = shutil.which("nft")

    async def get_current_routes(self) -> set[str]:
        """Query elements in the nftables set."""
        if not self._nft_path:
            return set(self._cached_routes)

        try:
            cmd = [self._nft_path, "list", "set", "inet", "tebypass", self.set_name]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                # Elements format parsing
                lines = stdout.decode().splitlines()
                elements: set[str] = set()
                in_elements = False
                for line in lines:
                    stripped = line.strip()
                    if "elements = {" in stripped:
                        in_elements = True
                        content = stripped.split("elements = {")[-1].rstrip("}")
                        for part in content.split(","):
                            p = part.strip()
                            if p:
                                elements.add(p)
                    elif in_elements:
                        content = stripped.rstrip("}")
                        for part in content.split(","):
                            p = part.strip()
                            if p:
                                elements.add(p)
                        if "}" in stripped:
                            in_elements = False
                self._cached_routes = elements
                return elements
        except Exception:
            pass

        return set(self._cached_routes)

    async def sync_routes(
        self,
        to_add: set[TargetCIDR],
        to_remove: set[str],
    ) -> SyncResult:
        """Add and remove elements in the nftables set."""
        start = time.perf_counter()
        added_count = 0
        removed_count = 0

        if self._nft_path:
            # Batch updates via nft -f
            commands: list[str] = []
            for item in to_add:
                commands.append(f"add element inet tebypass {self.set_name} {{ {item.cidr} }}")
            for cidr in to_remove:
                commands.append(f"delete element inet tebypass {self.set_name} {{ {cidr} }}")

            if commands:
                script = "\n".join(commands) + "\n"
                try:
                    proc = await asyncio.create_subprocess_exec(
                        self._nft_path,
                        "-f",
                        "-",
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, stderr = await proc.communicate(input=script.encode())
                    if proc.returncode == 0:
                        added_count = len(to_add)
                        removed_count = len(to_remove)
                    else:
                        return SyncResult(
                            driver_name=self.driver_name,
                            added_count=0,
                            removed_count=0,
                            total_active=len(self._cached_routes),
                            duration_ms=round((time.perf_counter() - start) * 1000.0, 2),
                            success=False,
                            error_message=stderr.decode().strip(),
                        )
                except Exception as ex:
                    return SyncResult(
                        driver_name=self.driver_name,
                        added_count=0,
                        removed_count=0,
                        total_active=len(self._cached_routes),
                        duration_ms=round((time.perf_counter() - start) * 1000.0, 2),
                        success=False,
                        error_message=str(ex),
                    )
        else:
            # Emulated mode when running outside Linux container with nftables
            for item in to_add:
                self._cached_routes.add(item.cidr)
                added_count += 1
            for cidr in to_remove:
                self._cached_routes.discard(cidr)
                removed_count += 1

        self._last_sync = datetime.now(UTC)
        return SyncResult(
            driver_name=self.driver_name,
            added_count=added_count,
            removed_count=removed_count,
            total_active=len(self._cached_routes),
            duration_ms=round((time.perf_counter() - start) * 1000.0, 2),
            success=True,
            error_message=None,
        )

    async def health_check(self) -> DriverStatus:
        """Inspect health of the virtual gateway driver."""
        has_nft = self._nft_path is not None
        details: dict[str, Any] = {
            "nft_available": has_nft,
            "set_name": self.set_name,
            "binary_path": self._nft_path or "none",
        }
        health = DriverHealth.HEALTHY if has_nft else DriverHealth.DEGRADED
        return DriverStatus(
            driver_name=self.driver_name,
            health=health,
            active_routes_count=len(self._cached_routes),
            last_sync=self._last_sync,
            details=details,
        )

    async def close(self) -> None:
        """Release driver resources."""
        pass
