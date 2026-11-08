"""Dynamic version and build metadata resolution for TeByPass."""

import os
import subprocess
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_VERSION = "0.1.0"


def _get_git_info() -> dict[str, str]:
    """Extract Git commit short hash and dirty state if repository exists."""
    git_dir = BASE_DIR / ".git"
    if not git_dir.exists():
        return {}

    try:
        commit = (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(BASE_DIR),
                stderr=subprocess.DEVNULL,
            )
            .decode("utf-8")
            .strip()
        )

        commit_count = (
            subprocess.check_output(
                ["git", "rev-list", "--count", "HEAD"],
                cwd=str(BASE_DIR),
                stderr=subprocess.DEVNULL,
            )
            .decode("utf-8")
            .strip()
        )

        # Check for tags on current commit
        try:
            tag = (
                subprocess.check_output(
                    ["git", "describe", "--tags", "--exact-match"],
                    cwd=str(BASE_DIR),
                    stderr=subprocess.DEVNULL,
                )
                .decode("utf-8")
                .strip()
            )
        except Exception:
            tag = ""

        return {
            "commit": commit,
            "commit_count": commit_count,
            "tag": tag,
        }
    except Exception:
        return {}


def get_version_info() -> dict[str, Any]:
    """Return comprehensive dictionary with semantic and dynamic build version."""
    env_version = os.environ.get("TEBYPASS_VERSION")
    env_commit = os.environ.get("TEBYPASS_GIT_COMMIT")

    git_info = _get_git_info()
    commit = env_commit or git_info.get("commit", "unknown")
    tag = git_info.get("tag", "")
    commit_count = git_info.get("commit_count", "0")

    if env_version:
        base_version = env_version
    elif tag:
        base_version = tag.lstrip("v")
    else:
        base_version = DEFAULT_VERSION

    # Build formatted dynamic string
    if tag:
        display_version = tag
    elif commit != "unknown":
        display_version = f"v{base_version}-rev{commit_count}.{commit}"
    else:
        display_version = f"v{base_version}"

    return {
        "base_version": base_version,
        "commit": commit,
        "commit_count": int(commit_count) if commit_count.isdigit() else 0,
        "tag": tag,
        "display_version": display_version,
    }


def get_version_string() -> str:
    """Return the display version string for logging, API titles, and UI."""
    return str(get_version_info()["display_version"])
