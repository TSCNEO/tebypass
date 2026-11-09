"""Run inside a disposable NET_ADMIN gateway container, never on the host namespace."""

import asyncio
import errno
import os
import socket
import subprocess
from pathlib import Path

from controller import dispatch, run_command
from wireguard_runtime import WireGuardRuntime


def checked(*args: str) -> None:
    subprocess.run(args, check=True, capture_output=True)


async def main() -> None:
    checked("nft", "-f", "/etc/nftables/tebypass.conf")
    runtime = WireGuardRuntime(run_command)
    await runtime.policy(True)
    result = await dispatch({"cmd": "sync", "add": ["198.51.100.7/32"], "remove": []})
    assert result.get("ok"), result
    listed = await dispatch({"cmd": "list"})
    assert listed.get("elements") == ["198.51.100.7/32"], listed
    route = subprocess.run(
        ["ip", "route", "get", "198.51.100.7", "mark", "0x100"], capture_output=True
    )
    assert route.returncode != 0, "Marked traffic must not fall back to main without a tunnel"
    checked("ip", "route", "get", "203.0.113.7")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        try:
            udp.sendto(b"tebypass-test", ("198.51.100.7", 9))
        except OSError as ex:
            assert ex.errno in {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EPERM}
        else:
            raise AssertionError("Selected traffic escaped without a tunnel")
    # Unselected traffic still uses the main routing table.
    await runtime.policy(False)
    checked("ip", "route", "get", "198.51.100.7", "mark", "0x100")
    await runtime.policy(True)
    assert (await dispatch({"cmd": "flush"})).get("ok")
    assert (await dispatch({"cmd": "list"})).get("elements") == []
    private = subprocess.run(
        ["wg", "genkey"], check=True, capture_output=True, text=True
    ).stdout.strip()
    peer_private = subprocess.run(
        ["wg", "genkey"], check=True, capture_output=True, text=True
    ).stdout
    peer_public = subprocess.run(
        ["wg", "pubkey"], input=peer_private, check=True, capture_output=True, text=True
    ).stdout.strip()
    runtime.source = Path("/tmp/wg-candidate.conf")
    fd = os.open(runtime.source, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(
            f"[Interface]\nPrivateKey = {private}\nAddress = 192.0.2.2/32\n"
            f"[Peer]\nPublicKey = {peer_public}\nAllowedIPs = 198.51.100.0/24\n"
            "Endpoint = 203.0.113.7:51820\n"
        )
    await runtime.reload()
    assert Path("/sys/class/net/wg0").exists()
    checked("ip", "route", "get", "198.51.100.7", "mark", "0x100")
    runtime.source.unlink()
    await runtime.reload()
    assert not Path("/sys/class/net/wg0").exists()
    unreachable = subprocess.run(
        ["ip", "route", "get", "198.51.100.7", "mark", "0x100"], capture_output=True
    )
    assert unreachable.returncode != 0
    print("WireGuard lifecycle kernel checks passed: start, route, stop, fail-closed")
    print(
        "Gateway kernel checks passed: selected fail-closed, normal main route, explicit fail-open, flush"
    )


asyncio.run(main())
