"""Release bundles pin the selected version and contain no local deployment files."""

import hashlib
from pathlib import Path

import pytest

from scripts.prepare_release import prepare


def test_bundle_pins_version_and_has_verifiable_checksums(tmp_path: Path) -> None:
    prepare("0.2.0-rc.1", tmp_path)
    compose = (tmp_path / "docker-compose.yml").read_text()
    assert compose.count("${TEBYPASS_IMAGE_TAG:-0.2.0-rc.1}") == 2
    assert "build:" not in compose
    assert not (tmp_path / ".env").exists()
    assert (tmp_path / "env.example").exists()
    assert not (tmp_path / ".env.example").exists()
    for line in (tmp_path / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        assert hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == digest
    assert "v0.2.0-rc.1/docs/OPERATIONS.md" in (tmp_path / "INSTALL.md").read_text()


def test_bundle_rejects_invalid_version_before_writing(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        prepare("../../invalid", tmp_path)
    assert not list(tmp_path.iterdir())
