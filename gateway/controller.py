#!/usr/bin/env python3
"""Control daemon for the TeByPass virtual gateway.

Listens on a Unix domain socket (``/data/gateway.sock`` by default) shared with the
main application through the ``/data`` volume. Every request is a single JSON line
and every reply is a single JSON line, which keeps the protocol trivial to speak
from either side:

    {"cmd": "status"}                            -> interface + nftables report
    {"cmd": "sync", "add": [...], "remove": [...]} -> atomic set reconciliation
    {"cmd": "list"}                              -> active elements of the set
    {"cmd": "flush"}                             -> empty the set
    {"cmd": "ping"}                              -> liveness probe

The daemon only ever talks to local binaries (``nft``, ``wg``, ``ip``) and validates
every prefix before it reaches a command, so a request cannot inject nftables syntax.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import shutil
import signal
import time
from pathlib import Path
from typing import Any

LOG = logging.getLogger("tebypass.gateway")

DEFAULT_SOCKET_PATH = "/data/gateway.sock"
SOCKET_MODE = 0o600
TABLE = "inet tebypass"
SET_NAME = os.environ.get("TEBYPASS_GATEWAY_SET", "tebypass_targets")
WG_INTERFACE = os.environ.get("TEBYPASS_GATEWAY_WG_IFACE", "wg0")
MARK = "0x100"
NFT_BIN = shutil.which("nft") or "/usr/sbin/nft"
IP_BIN = shutil.which("ip") or "/sbin/ip"
WG_BIN = shutil.which("wg") or "/usr/bin/wg"

MAX_LINE_BYTES = 256 * 1024
CMD_TIMEOUT_SECONDS = 10.0


class GatewayError(RuntimeError):
    """Raised when a request cannot be satisfied by the local tooling."""


# --------------------------------------------------------------------------- #
# nftables primitives
# --------------------------------------------------------------------------- #


async def run_command(*args: str, stdin: str | None = None) -> tuple[int, str, str]:
    """Run a local command without a shell and return (code, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(stdin.encode() if stdin is not None else None),
            timeout=CMD_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", f"command timed out: {' '.join(args)}"
    return (
        proc.returncode or 0,
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
    )


def normalize_cidr(value: str) -> str:
    """Return a canonical IPv4 prefix, rejecting anything that is not one."""
    if not isinstance(value, str):
        raise GatewayError(f"invalid CIDR type: {type(value).__name__}")
    text = value.strip()
    if not text:
        raise GatewayError("empty CIDR")
    if "/" not in text:
        text = f"{text}/32"
    try:
        network = ipaddress.ip_network(text, strict=False)
    except ValueError as ex:
        raise GatewayError(f"invalid CIDR '{value}': {ex}") from ex
    if network.version != 4:
        raise GatewayError(f"only IPv4 targets are supported: {value}")
    return str(network)


def canonical_elements(elements: list[str]) -> list[str]:
    """Normalise nftables output to CIDR form.

    An interval set stores a host address without its ``/32`` suffix, so a target
    added as ``198.51.100.7/32`` comes back as ``198.51.100.7``. Canonicalising the
    output keeps the diff against the database stable across sync cycles.
    """
    canonical: set[str] = set()
    for element in elements:
        try:
            canonical.add(normalize_cidr(element))
        except GatewayError:
            canonical.add(element)
    return sorted(canonical)


def parse_set_elements(payload: str) -> list[str]:
    """Extract the element list from ``nft -j list set`` output."""
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as ex:
        raise GatewayError(f"cannot parse nftables JSON: {ex}") from ex

    elements: list[str] = []
    for item in document.get("nftables", []):
        if not isinstance(item, dict):
            continue
        entry = item.get("set")
        if isinstance(entry, dict) and isinstance(entry.get("elem"), list):
            elements.extend(str(element) for element in entry["elem"])
    return sorted(set(elements))


async def list_elements() -> list[str]:
    """Return the CIDRs currently present in the target set."""
    code, out, err = await run_command(NFT_BIN, "-j", "list", "set", TABLE, SET_NAME)
    if code != 0:
        raise GatewayError(f"nft list set failed: {err.strip() or f'exit {code}'}")
    return parse_set_elements(out)


async def apply_ruleset(script: str) -> None:
    """Apply a ruleset fragment in a single atomic nftables transaction."""
    code, _out, err = await run_command(NFT_BIN, "-f", "-", stdin=script)
    if code != 0:
        raise GatewayError(f"nft transaction failed: {err.strip() or f'exit {code}'}")


def build_delta_script(adds: list[str], removes: list[str]) -> str:
    """Compose the minimal add/remove transaction for two disjoint element lists."""
    statements: list[str] = []
    if removes:
        statements.append(f"delete element {TABLE} {SET_NAME} {{ {', '.join(removes)} }}")
    if adds:
        statements.append(f"add element {TABLE} {SET_NAME} {{ {', '.join(adds)} }}")
    return "\n".join(statements) + "\n" if statements else ""


def build_replace_script(elements: list[str]) -> str:
    """Compose a full replace transaction, used when a delta would collide."""
    statements = [f"flush set {TABLE} {SET_NAME}"]
    if elements:
        statements.append(f"add element {TABLE} {SET_NAME} {{ {', '.join(elements)} }}")
    return "\n".join(statements) + "\n"


# --------------------------------------------------------------------------- #
# State inspection
# --------------------------------------------------------------------------- #


async def wireguard_state() -> dict[str, Any]:
    """Report whether the WireGuard interface exists and how fresh its handshake is."""
    interface = Path(f"/sys/class/net/{WG_INTERFACE}")
    if not interface.exists():
        return {"present": False, "handshake_age": None}

    age: int | None = None
    code, out, _err = await run_command(WG_BIN, "show", WG_INTERFACE, "latest-handshakes")
    if code == 0 and out.strip():
        newest = 0
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                newest = max(newest, int(parts[1]))
        if newest:
            age = max(0, int(time.time()) - newest)
    return {"present": True, "handshake_age": age}


async def mark_rule_present() -> bool:
    """Return True when the fwmark policy rule is installed."""
    code, out, _err = await run_command(IP_BIN, "rule", "show")
    return code == 0 and "fwmark 0x100" in out.replace("fwmark 0x0100", "fwmark 0x100")


async def nftables_state() -> dict[str, Any]:
    """Report the table, the target set and its current elements."""
    code, out, _err = await run_command(NFT_BIN, "-j", "list", "table", TABLE)
    if code != 0:
        return {"table": False, "set": False, "elements": []}
    try:
        document = json.loads(out)
    except json.JSONDecodeError:
        return {"table": True, "set": False, "elements": []}

    has_set = any(
        isinstance(item, dict) and isinstance(item.get("set"), dict) for item in document.get("nftables", [])
    )
    if not has_set:
        return {"table": True, "set": False, "elements": []}
    try:
        elements = canonical_elements(parse_set_elements(out))
    except GatewayError:
        elements = []
    return {"table": True, "set": True, "elements": elements}


async def collect_status() -> dict[str, Any]:
    """Build the status payload consumed by the application driver."""
    wireguard = await wireguard_state()
    nft_state = await nftables_state()
    rule = await mark_rule_present()
    elements: list[str] = nft_state["elements"]
    healthy = bool(wireguard["present"] and nft_state["set"] and rule)
    return {
        "ok": healthy,
        "interface": WG_INTERFACE,
        "wireguard": wireguard,
        "nftables": {
            "table": nft_state["table"],
            "set": nft_state["set"],
            "set_name": SET_NAME,
            "elements_count": len(elements),
            "mark_rule": rule,
        },
        "elements": elements,
    }


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def _string_list(message: dict[str, Any], key: str) -> list[str]:
    raw = message.get(key, [])
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise GatewayError(f"'{key}' must be a list of CIDRs")
    return [normalize_cidr(str(item)) for item in raw]


async def handle_status(_message: dict[str, Any]) -> dict[str, Any]:
    return await collect_status()


async def handle_list(_message: dict[str, Any]) -> dict[str, Any]:
    elements = canonical_elements(await list_elements())
    return {"ok": True, "elements": elements, "total": len(elements)}


async def handle_flush(_message: dict[str, Any]) -> dict[str, Any]:
    await apply_ruleset(f"flush set {TABLE} {SET_NAME}\n")
    return {"ok": True, "total": 0, "elements": []}


async def handle_sync(message: dict[str, Any]) -> dict[str, Any]:
    """Reconcile the target set atomically, reporting what really changed."""
    started = time.perf_counter()
    requested_adds = set(_string_list(message, "add"))
    requested_removes = set(_string_list(message, "remove"))

    current = set(canonical_elements(await list_elements()))
    adds = sorted(requested_adds - current)
    removes = sorted(current & requested_removes)
    desired = sorted((current - set(removes)) | set(adds))

    if adds or removes:
        try:
            await apply_ruleset(build_delta_script(adds, removes))
        except GatewayError as delta_error:
            # An interval set can reject a delta that overlaps an existing range.
            # Replacing the whole set in one transaction is still atomic and always
            # converges to the requested state.
            LOG.warning("delta transaction failed (%s); replacing the set", delta_error)
            try:
                await apply_ruleset(build_replace_script(desired))
            except GatewayError:
                raise delta_error from None

    try:
        total = len(canonical_elements(await list_elements()))
    except GatewayError:
        total = len(desired)

    return {
        "ok": True,
        "added_count": len(adds),
        "removed_count": len(removes),
        "total_active": total,
        "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
        "elements": desired,
    }


COMMANDS = {
    "status": handle_status,
    "list": handle_list,
    "flush": handle_flush,
    "sync": handle_sync,
}


async def dispatch(message: dict[str, Any]) -> dict[str, Any]:
    """Route one decoded request to its handler, never raising to the caller."""
    command = str(message.get("cmd", "")).strip().lower()
    if command == "ping":
        return {"ok": True, "pong": True}
    handler = COMMANDS.get(command)
    if handler is None:
        return {"ok": False, "error": f"unknown command '{command}'", "commands": sorted([*COMMANDS, "ping"])}
    try:
        return await handler(message)
    except GatewayError as ex:
        return {"ok": False, "error": str(ex)}
    except Exception as ex:  # defensive: a handler bug must not kill the daemon
        LOG.exception("command '%s' failed", command)
        return {"ok": False, "error": f"{type(ex).__name__}: {ex}"}


# --------------------------------------------------------------------------- #
# Unix socket server
# --------------------------------------------------------------------------- #


class ControlServer:
    """Async Unix domain socket server speaking the JSON-line protocol."""

    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        path = Path(self.socket_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() or path.is_socket():
            path.unlink(missing_ok=True)

        old_umask = os.umask(0o077)
        try:
            self._server = await asyncio.start_unix_server(
                self._handle_client,
                path=self.socket_path,
                limit=MAX_LINE_BYTES,
            )
        finally:
            os.umask(old_umask)
        os.chmod(self.socket_path, SOCKET_MODE)
        LOG.info("control socket listening at %s (mode %o)", self.socket_path, SOCKET_MODE)

    async def serve_forever(self) -> None:
        if self._server is None:
            raise RuntimeError("server is not started")
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        Path(self.socket_path).unlink(missing_ok=True)
        LOG.info("control socket closed")

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=CMD_TIMEOUT_SECONDS)
            if not line:
                return
            try:
                message = json.loads(line)
            except json.JSONDecodeError as ex:
                response: dict[str, Any] = {"ok": False, "error": f"invalid JSON: {ex}"}
            else:
                if not isinstance(message, dict):
                    response = {"ok": False, "error": "request must be a JSON object"}
                else:
                    response = await dispatch(message)
            writer.write(json.dumps(response).encode("utf-8") + b"\n")
            await writer.drain()
        except TimeoutError:
            LOG.warning("client timed out before sending a request")
        except (BrokenPipeError, ConnectionResetError):
            LOG.warning("client disconnected before the reply was written")
        except Exception:
            LOG.exception("unhandled error while serving a client")
        finally:
            writer.close()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                await writer.wait_closed()


async def amain() -> int:
    logging.basicConfig(
        level=os.environ.get("TEBYPASS_LOG_LEVEL", "INFO").upper(),
        format="[gateway-controller] %(levelname)s %(message)s",
    )
    socket_path = os.environ.get("TEBYPASS_GATEWAY_SOCKET", DEFAULT_SOCKET_PATH)
    server = ControlServer(socket_path)
    await server.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    LOG.info("gateway control daemon ready (table=%s, set=%s)", TABLE, SET_NAME)
    await stop.wait()
    await server.close()
    return 0


def main() -> int:
    try:
        return asyncio.run(amain())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
