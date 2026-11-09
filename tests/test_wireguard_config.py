"""Reject executable hooks and validate the sidecar's config normalization."""

import pytest

from app.core.wireguard_config import validate_config

VALID = """[Interface]
PrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
Address = 192.0.2.2/32
DNS = 192.0.2.1
[Peer]
PublicKey = AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=
AllowedIPs = 198.51.100.0/24
Endpoint = edge.example.net:51820
"""


def test_config_forces_split_routing_and_removes_dns() -> None:
    result = validate_config(VALID)
    assert "Table = off" in result
    assert "DNS" not in result


@pytest.mark.parametrize("extra", ["PostUp = echo unsafe", "Table = auto", "PrivateKey = invalid"])
def test_config_rejects_hooks_table_override_and_invalid_keys(extra: str) -> None:
    content = VALID.replace("[Peer]", extra + "\n[Peer]")
    with pytest.raises(ValueError):
        validate_config(content)
