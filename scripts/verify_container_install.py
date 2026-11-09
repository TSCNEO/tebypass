"""Smoke-test an installation pulled from the registry; never print its admin key."""

import http.cookiejar
import json
import os
import subprocess
import urllib.error
import urllib.request
from typing import Any


def main() -> None:
    base = "http://127.0.0.1:9384"
    client = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )

    def request(path: str, method: str = "GET", data: Any = None, origin: str | None = None) -> Any:
        headers = {"Content-Type": "application/json"}
        if origin is not None:
            headers["Origin"] = origin
        req = urllib.request.Request(
            base + path,
            data=json.dumps(data).encode() if data is not None else None,
            method=method,
            headers=headers,
        )
        with client.open(req, timeout=10) as response:
            return json.load(response)

    assert request("/healthz")["status"] == "ok"
    try:
        request("/api/v1/settings")
    except urllib.error.HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError("Anonymous settings access was allowed")
    token = subprocess.check_output(
        ["docker", "compose", "exec", "-T", "tebypass", "cat", "/data/admin_token"], text=True
    ).strip()
    request("/auth/session", "POST", {"token": token, "browser_origin": base}, base)
    settings = request("/api/v1/settings")
    assert request("/api/v1/settings", "PUT", {}, base) == settings
    version = request("/api/v1/version")
    assert version["commit"] == os.environ["EXPECTED_COMMIT"][:7]
    try:
        request("/api/v1/settings", "PUT", {}, "https://other.example.org")
    except urllib.error.HTTPError as error:
        assert error.code == 403
    else:
        raise AssertionError("Foreign origin accepted")
    request("/api/v1/settings", "PUT", {"sync_interval_seconds": 0}, base)
    subprocess.run(
        [
            "docker",
            "compose",
            "up",
            "-d",
            "--force-recreate",
            "--wait",
            "--wait-timeout",
            "90",
            "tebypass",
        ],
        check=True,
    )
    assert request("/api/v1/settings")["sync_interval_seconds"] == 0
    persisted = subprocess.check_output(
        ["docker", "compose", "exec", "-T", "tebypass", "cat", "/data/admin_token"], text=True
    ).strip()
    assert persisted == token
    print("Registry installation: health, auth, save, CSRF, revision and persistence OK")


if __name__ == "__main__":
    main()
