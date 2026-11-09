"""Dynamic version and build metadata resolution for TeByPass."""

import os
import subprocess
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_VERSION = "0.2.0"


def _read_git_head_pure(git_dir: Path) -> str:
    """Parse .git directory directly without needing git binary."""
    try:
        if git_dir.is_file():
            txt = git_dir.read_text(encoding="utf-8").strip()
            if txt.startswith("gitdir:"):
                git_dir = (git_dir.parent / txt.split(":", 1)[1].strip()).resolve()

        head_file = git_dir / "HEAD"
        if not head_file.exists():
            return ""

        head_txt = head_file.read_text(encoding="utf-8").strip()
        if not head_txt.startswith("ref:"):
            return head_txt[:7]

        ref_path = head_txt.split(":", 1)[1].strip()
        ref_file = git_dir / ref_path
        if ref_file.exists():
            return ref_file.read_text(encoding="utf-8").strip()[:7]

        packed_file = git_dir / "packed-refs"
        if packed_file.exists():
            for line in packed_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("^"):
                    continue
                parts = line.split()
                if len(parts) >= 2 and parts[1] == ref_path:
                    return parts[0][:7]
    except Exception:
        pass
    return ""


def _get_git_info() -> dict[str, str]:
    """Extract Git commit short hash and dirty state if repository exists."""
    git_dir = BASE_DIR / ".git"
    if not git_dir.exists():
        build_stamp = BASE_DIR / "BUILD_COMMIT"
        if build_stamp.exists():
            c = build_stamp.read_text(encoding="utf-8").strip()
            if c:
                tag_stamp = BASE_DIR / "BUILD_TAG"
                tag = tag_stamp.read_text().strip() if tag_stamp.exists() else ""
                return {"commit": c[:7], "commit_count": "0", "tag": tag}
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
        pure_commit = _read_git_head_pure(git_dir)
        if pure_commit:
            return {"commit": pure_commit, "commit_count": "0", "tag": ""}
        return {}


def get_version_info() -> dict[str, Any]:
    """Return comprehensive dictionary with semantic and dynamic build version."""
    env_version = os.environ.get("TEBYPASS_VERSION")
    env_commit = os.environ.get("TEBYPASS_GIT_COMMIT")

    git_info = _get_git_info()
    if env_commit and env_commit.strip().lower() not in {"", "unknown"}:
        commit = env_commit.strip()
    else:
        commit = git_info.get("commit", "unknown")
    tag = git_info.get("tag", "")
    commit_count = git_info.get("commit_count", "0")

    if env_version:
        base_version = env_version
    elif tag:
        base_version = tag.lstrip("v")
    else:
        stamp = BASE_DIR / "BUILD_VERSION"
        base_version = stamp.read_text().strip() if stamp.exists() else DEFAULT_VERSION

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
