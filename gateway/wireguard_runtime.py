"""Own the WireGuard lifecycle and policy routing, independently of Python web runtime."""

import asyncio
import json
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
        if not await self.policy_matches(fail_closed):
            raise RuntimeError("Gateway policy verification failed")

    async def policy_matches(self, fail_closed: bool) -> bool:
        """Observe the exact rule and fallback route, rather than trusting command exit codes."""
        code, rules, _ = await self.run("ip", "-j", "rule", "show")
        route_code, routes, _ = await self.run("ip", "-j", "route", "show", "table", "100")
        if code or route_code:
            raise RuntimeError("Cannot verify gateway routing policy")
        rule_entries = json.loads(rules)
        route_entries = json.loads(routes)
        has_rule = any(
            str(rule.get("fwmark")) in {"0x100", "256"}
            and str(rule.get("table")) == "100"
            and rule.get("priority") == 100
            and str(rule.get("fwmask", "0xffffffff")) in {"0xffffffff", "4294967295"}
            for rule in rule_entries
        )
        has_fallback = any(
            route.get("type") == "unreachable"
            and route.get("dst") == "default"
            and route.get("metric") == 32760
            for route in route_entries
        )
        return has_rule and has_fallback == fail_closed

    async def stop(self) -> None:
        if self.is_running():
            await self.command("wg-quick", "down", str(self.active))

    def is_running(self) -> bool:
        return Path("/sys/class/net/wg0").exists()

    @staticmethod
    def _write_file(path: Path, content: str) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(content)
            os.replace(temp, path)
        finally:
            Path(temp).unlink(missing_ok=True)

    async def _start(self) -> None:
        await self.command("wg-quick", "up", str(self.active))
        await self.command(
            "ip", "route", "replace", "default", "dev", "wg0", "metric", "10", "table", "100"
        )

    async def reload(self) -> None:
        async with self.lock:
            # Validate before touching the current tunnel.
            content = validate_config(self.source.read_text()) if self.source.is_file() else None
            last_good = self.source.with_name("wg0.last-good.conf")
            backup = self.active if self.active.is_file() else last_good
            previous = backup.read_text() if backup.is_file() else None
            was_running = self.is_running()
            restore_running = was_running or (backup == last_good and previous is not None)
            await self.stop()
            if content is None:
                self.active.unlink(missing_ok=True)
                last_good.unlink(missing_ok=True)
                return
            try:
                self._write_file(self.active, content)
                await self._start()
                self._write_file(last_good, content)
            except Exception as error:
                try:
                    await self.stop()
                    if previous is None:
                        self.active.unlink(missing_ok=True)
                    else:
                        self._write_file(self.active, previous)
                        if restore_running:
                            await self._start()
                except Exception as rollback_error:
                    raise RuntimeError(
                        "WireGuard update failed; rollback failed"
                    ) from rollback_error
                raise RuntimeError(
                    "Gateway network command failed; previous state restored"
                ) from error
