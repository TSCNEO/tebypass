"""Strict, shell-free WireGuard config validation shared with the sidecar."""

import base64
import ipaddress
import re


def validate_config(content: str) -> str:
    if len(content.encode()) > 65536:
        raise ValueError("WireGuard configuration exceeds 64 KiB")
    section = ""
    interface: dict[str, str] = {}
    peers: list[dict[str, str]] = []
    allowed = {
        "Interface": {"PrivateKey", "Address", "ListenPort", "MTU", "DNS", "Table"},
        "Peer": {"PublicKey", "PresharedKey", "AllowedIPs", "Endpoint", "PersistentKeepalive"},
    }
    for raw in content.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("["):
            section = line.strip("[]")
            if section not in allowed:
                raise ValueError("Only Interface and Peer sections are allowed")
            if section == "Interface":
                if interface or peers:
                    raise ValueError("Exactly one Interface must precede peers")
            else:
                peers.append({})
            continue
        if "=" not in line or section not in allowed:
            raise ValueError("Malformed WireGuard configuration")
        key, value = (part.strip() for part in line.split("=", 1))
        if key not in allowed[section]:
            raise ValueError("WireGuard hooks and unknown directives are not allowed")
        record = interface if section == "Interface" else peers[-1]
        if key in record:
            raise ValueError("Duplicate WireGuard directive")
        record[key] = value
        if key in {"PrivateKey", "PublicKey", "PresharedKey"}:
            try:
                if len(base64.b64decode(value, validate=True)) != 32:
                    raise ValueError("Invalid WireGuard key")
            except Exception as ex:
                raise ValueError("Invalid WireGuard key") from ex
        elif key in {"Address", "AllowedIPs"}:
            for address in value.split(","):
                ipaddress.ip_interface(address.strip())
        elif key in {"ListenPort", "MTU", "PersistentKeepalive"}:
            if not value.isdigit() or not 0 <= int(value) <= 65535:
                raise ValueError("Invalid numeric WireGuard value")
        elif key == "Endpoint":
            if not re.fullmatch(r"(?:[a-zA-Z0-9.-]+|\[[0-9a-fA-F:]+\]):[0-9]{1,5}", value):
                raise ValueError("Invalid WireGuard endpoint")
        elif key == "Table" and value != "off":
            raise ValueError("WireGuard Table must be off for split routing")
    if "PrivateKey" not in interface or not peers:
        raise ValueError("Interface key and at least one Peer are required")
    if any(not {"PublicKey", "AllowedIPs"} <= peer.keys() for peer in peers):
        raise ValueError("Each Peer needs PublicKey and AllowedIPs")
    # DNS stays managed by the client network; never execute external hooks/resolvconf.
    lines = ["[Interface]", "Table = off"]
    lines += [f"{key} = {value}" for key, value in interface.items() if key not in {"Table", "DNS"}]
    for peer in peers:
        lines += ["", "[Peer]", *[f"{key} = {value}" for key, value in peer.items()]]
    return "\n".join(lines) + "\n"
