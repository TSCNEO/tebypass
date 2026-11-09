"""Tests for the HayAHora / StopLiga feed parser and entry classification."""

import json

from app.core.feeds import FeedManager, ParsedTarget


def _values(targets: list[ParsedTarget]) -> list[str]:
    return [target.value for target in targets]


def test_hayahora_global_signal_controls_contribution() -> None:
    from tests.test_community_snapshot import payload

    manager = FeedManager()
    assert manager.parse_content(json.dumps(payload(False)), "hayahora") == []
    assert _values(manager.parse_content(json.dumps(payload(True)), "hayahora")) == [
        "198.51.100.7/32"
    ]


def test_force_overrides_global_signal_but_keeps_recent_destinations() -> None:
    from tests.test_community_snapshot import payload

    assert _values(FeedManager().parse_content(json.dumps(payload(False)), "hayahora", True)) == [
        "198.51.100.7/32"
    ]


def test_missing_history_does_not_create_active_destinations() -> None:
    from tests.test_community_snapshot import payload

    data = payload(True)
    data["data"] = [{"ip": "192.0.2.7", "isp": "ISP_A"}]
    assert FeedManager().parse_content(json.dumps(data), "hayahora") == []


def test_parse_entry_classifies_rfc5737_cidr() -> None:
    """RFC 5737 prefixes are normalized as CIDR targets."""
    manager = FeedManager()

    cidr = manager.parse_entry("198.51.100.0/24")
    assert cidr is not None
    assert cidr.value == "198.51.100.0/24"
    assert cidr.destination_type == "cidr"

    single = manager.parse_entry("203.0.113.9")
    assert single is not None
    assert single.value == "203.0.113.9/32"

    fqdn = manager.parse_entry("Edge-Node-01.Example.net.")
    assert fqdn is not None
    assert fqdn.value == "edge-node-01.example.net"
    assert fqdn.destination_type == "fqdn"

    assert manager.parse_entry("# comment") is None
    assert manager.parse_entry("not a target") is None


def test_plaintext_and_legacy_json_still_parse() -> None:
    """Backwards compatibility with plaintext lists and {domains, ips} documents."""
    manager = FeedManager()

    plain = manager.parse_content(
        "198.51.100.0/24\nsport-stream.example.org\n",
        feed_format="plaintext",
    )
    assert _values(plain) == ["198.51.100.0/24", "sport-stream.example.org"]

    legacy = manager.parse_content(
        json.dumps({"domains": ["feed.example.net"], "ips": ["192.0.2.5"]}),
        feed_format="json",
    )
    assert _values(legacy) == ["feed.example.net", "192.0.2.5/32"]
