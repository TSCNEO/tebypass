"""Destination capabilities shared by ingestion and router selection."""

import ipaddress

IPV4_ONLY_DRIVERS = frozenset({"virtual_gateway", "mikrotik", "omada"})


def validate_destination(value: str, destination_type: str, driver: str) -> None:
    """Reject explicit unsupported networks before storing or applying them."""
    if (
        destination_type == "cidr"
        and driver in IPV4_ONLY_DRIVERS
        and ipaddress.ip_network(value, strict=False).version != 4
    ):
        raise ValueError(f"Driver {driver} supports IPv4 only")
