"""Own the WireGuard lifecycle and policy routing, independently of Python web runtime."""

import asyncio
import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

try:
    from wireguard_config import validate_config
except ImportError:
    from app.core.wireguard_config import validate_config

Run = Callable[..., Awaitable[tuple[int, str, str]]]


class WireGuardRuntime:
    def __init__(self, run: Run) -> None:
        self.run = run
        self.source = Path(os.environ.get("TEBYPASS_WG_DIR", "/data/wireguard")) / "wg0.conf"
        self.active = Path("/etc/wireguard/wg0.conf")
        self.lock = asyncio.Lock()

    async def command(self, *args: str) -> None:
        code, _, _ = await self.run(*args)
        if code:
            raise RuntimeError("Gateway network command failed")

    async def policy(self, fail_closed: bool = True) -> None:
        # A high-metric unreachable default prevents fallback to the main table.
        await self.command(
            "ip", "route", "replace", "unreachable", "default", "metric", "32760", "table", "100"
        )
        code, rules, _ = await self.run("ip", "rule", "show")
        if code:
            raise RuntimeError("Cannot inspect gateway policy rules")
        if "fwmark 0x100" not in rules:
            await self.command(
                "ip", "rule", "add", "priority", "100", "fwmark", "0x100", "lookup", "100"
            )
        if not fail_closed:
            await self.command(
                "ip", "route", "del", "unreachable", "default", "metric", "32760", "table", "100"
            )

    async def stop(self) -> None:
        if Path("/sys/class/net/wg0").exists():
            await self.command("wg-quick", "down", str(self.active))

    async def reload(self) -> None:
        async with self.lock:
            # Validate before touching the current tunnel.
            content = validate_config(self.source.read_text()) if self.source.is_file() else None
            await self.stop()
            if content is None:
                self.active.unlink(missing_ok=True)
                return
            self.active.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(dir=self.active.parent)
            with os.fdopen(fd, "w") as handle:
                handle.write(content)
            os.replace(temp, self.active)
            await self.command("wg-quick", "up", str(self.active))
            await self.command(
                "ip", "route", "replace", "default", "dev", "wg0", "metric", "10", "table", "100"
            )
