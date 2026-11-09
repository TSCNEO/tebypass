"""Deployment helper: observed connection, no wildcard, idempotence and rollback."""

from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from scripts import setup_proxy


def test_ready_access_needs_no_docker_or_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_proxy, "probe", lambda url: ({"scheme": "https"}, "marker"))
    docker = Mock(side_effect=AssertionError("No Docker command expected"))
    monkeypatch.setattr(setup_proxy, "command", docker)
    plan = setup_proxy.inspect_access("https://tebypass.example.org", tmp_path, "tebypass")
    assert plan["state"] == "ready"
    assert (
        setup_proxy.apply_access(
            "https://tebypass.example.org", tmp_path, "tebypass", "tebypass", plan
        )
        == plan
    )
    assert not (tmp_path / ".env").exists()


def test_detects_only_connection_for_the_fresh_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_proxy, "probe", lambda url: ({"scheme": "http"}, "fresh-marker"))
    monkeypatch.setattr(
        setup_proxy,
        "command",
        lambda args, project: (
            'INFO: 198.51.100.8:123 - "GET /healthz?setup_probe=old-marker HTTP/1.1" 200 OK\n'
            'INFO: 192.0.2.10:123 - "GET /healthz?setup_probe=fresh-marker HTTP/1.1" 200 OK\n'
        ),
    )
    plan = setup_proxy.inspect_access("https://tebypass.example.org", tmp_path, "tebypass")
    assert plan["proxy_connection"] == "192.0.2.10"
    assert not (tmp_path / ".env").exists()


def test_no_log_proof_refuses_to_guess_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_proxy, "probe", lambda url: ({"scheme": "http"}, "fresh-marker"))
    monkeypatch.setattr(setup_proxy, "command", lambda args, project: "")
    with pytest.raises(setup_proxy.SetupError, match="identificar"):
        setup_proxy.inspect_access("https://tebypass.example.org", tmp_path, "tebypass")


def test_apply_preserves_other_settings_and_verifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("TEBYPASS_PORT=9384\n")
    monkeypatch.setattr(setup_proxy, "command", lambda args, project: "")
    monkeypatch.setattr(setup_proxy, "probe", lambda url: ({"scheme": "https"}, "marker"))
    plan: dict[str, Any] = {"state": "needs_configuration", "proxy_connection": "192.0.2.10"}
    result = setup_proxy.apply_access(
        "https://tebypass.example.org", tmp_path, "tebypass", "tebypass", plan
    )
    assert result["state"] == "applied"
    content = (tmp_path / ".env").read_text()
    assert "TEBYPASS_PORT=9384" in content
    assert "TEBYPASS_TRUSTED_PROXIES=127.0.0.1,192.0.2.10" in content
    assert (tmp_path / ".env").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / ".env.before-proxy-setup").read_text() == "TEBYPASS_PORT=9384\n"


def test_failed_verification_restores_previous_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = "TEBYPASS_PORT=9384\n"
    (tmp_path / ".env").write_text(original)
    recreate = Mock(return_value="")
    monkeypatch.setattr(setup_proxy, "command", recreate)
    monkeypatch.setattr(setup_proxy, "probe", Mock(side_effect=RuntimeError("unavailable")))
    monkeypatch.setattr("scripts.setup_proxy.time.sleep", lambda seconds: None)
    with pytest.raises(setup_proxy.SetupError, match="restauró"):
        setup_proxy.apply_access(
            "https://tebypass.example.org",
            tmp_path,
            "tebypass",
            "tebypass",
            {"state": "needs_configuration", "proxy_connection": "192.0.2.10"},
        )
    assert (tmp_path / ".env").read_text() == original
    assert recreate.call_count == 2


def test_existing_wildcard_is_not_silently_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = 'TEBYPASS_TRUSTED_PROXIES="*"\n'
    (tmp_path / ".env").write_text(original)
    docker = Mock(side_effect=AssertionError("No recreation expected"))
    monkeypatch.setattr(setup_proxy, "command", docker)
    with pytest.raises(setup_proxy.SetupError, match="global"):
        setup_proxy.apply_access(
            "https://tebypass.example.org",
            tmp_path,
            "tebypass",
            "tebypass",
            {"state": "needs_configuration", "proxy_connection": "192.0.2.10"},
        )
    assert (tmp_path / ".env").read_text() == original
