"""Tests for the HayAHora / StopLiga feed parser and entry classification."""

import json

from app.core.feeds import FeedManager, ParsedTarget

HAYAHORA_PAYLOAD = {
    "lastUpdate": "2026-11-09 07:07:25Z",
    "data": [
        {
            "ip": "198.51.100.10",
            "isp": "ISP_A",
            "description": "Cloudflare",
            "stateChanges": [
                {"timestamp": "2026-11-08 21:27:55Z", "state": True},
                {"timestamp": "2026-11-08 23:09:05Z", "state": False},
            ],
        },
        {
            "ip": "203.0.113.20",
            "isp": "ISP_B",
            "description": "Edge cache",
            "stateChanges": [
                {"timestamp": "2026-11-08 23:09:05Z", "state": False},
                {"timestamp": "2026-11-09 06:00:00Z", "state": True},
            ],
        },
    ],
}


def _values(targets: list[ParsedTarget]) -> list[str]:
    return [target.value for target in targets]


def test_hayahora_only_active_when_force_active_disabled() -> None:
    """The latest stateChange decides whether an entry is currently blocked."""
    manager = FeedManager()
    payload = json.dumps(HAYAHORA_PAYLOAD)

    strict = manager.parse_content(payload, feed_format="hayahora", force_active=False)
    assert _values(strict) == ["203.0.113.20/32"]

    forced = manager.parse_content(payload, feed_format="hayahora", force_active=True)
    assert _values(forced) == ["198.51.100.10/32", "203.0.113.20/32"]


def test_hayahora_isp_filter_is_exact_and_case_insensitive() -> None:
    """The ISP filter matches the isp field exactly, ignoring case."""
    manager = FeedManager()
    payload = json.dumps(HAYAHORA_PAYLOAD)

    filtered = manager.parse_content(
        payload,
        feed_format="hayahora",
        force_active=True,
        isp_filter="isp_a",
    )
    assert _values(filtered) == ["198.51.100.10/32"]

    unmatched = manager.parse_content(
        payload,
        feed_format="hayahora",
        force_active=True,
        isp_filter="ISP_A_PARTIAL",
    )
    assert unmatched == []


def test_hayahora_is_auto_detected_on_json_feeds() -> None:
    """A HayAHora document parsed as plain json is still recognised by structure."""
    manager = FeedManager()
    targets = manager.parse_content(json.dumps(HAYAHORA_PAYLOAD), feed_format="json")
    assert len(targets) == 2
    assert "Cloudflare · ISP_A" in targets[0].description


def test_hayahora_entry_without_state_changes_is_kept() -> None:
    """Entries without history are kept: absence of state is not proof of normality."""
    manager = FeedManager()
    payload = json.dumps({"data": [{"ip": "192.0.2.7", "isp": "ISP_C"}]})

    targets = manager.parse_content(payload, feed_format="hayahora", force_active=False)
    assert _values(targets) == ["192.0.2.7/32"]


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
