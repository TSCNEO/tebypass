"""WireGuard client configuration upload and inspection endpoints."""

import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.models.schemas import (
    WireGuardStatusResponse,
    WireGuardUploadRequest,
    WireGuardUploadResponse,
)

router = APIRouter(prefix="/api/v1/gateway", tags=["Gateway"])

_PREVIEW_LINES = 5
_REDACTED_SUFFIX = " = [redacted]"
_SENSITIVE_PREFIXES = ("privatekey", "presharedkey")


def _config_path() -> Path:
    return Path(settings.wireguard_config_path)


def _redact(line: str) -> str:
    """Mask any line holding a private or preshared key."""
    stripped = line.strip()
    lowered = stripped.lower()
    for prefix in _SENSITIVE_PREFIXES:
        if lowered.startswith(prefix):
            key_name = stripped.split("=", 1)[0].strip() or "PrivateKey"
            return f"{key_name}{_REDACTED_SUFFIX}"
    return line


def _build_preview(content: str) -> str:
    lines = content.splitlines()[:_PREVIEW_LINES]
    return "\n".join(_redact(line) for line in lines)


def _uploaded_at(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()


@router.get("/wireguard", response_model=WireGuardStatusResponse)
async def get_wireguard_config() -> WireGuardStatusResponse:
    """Report whether a WireGuard configuration is uploaded, without leaking secrets."""
    path = _config_path()
    if not path.is_file():
        return WireGuardStatusResponse(configured=False, path=str(path))

    content = path.read_text(encoding="utf-8", errors="replace")
    return WireGuardStatusResponse(
        configured=True,
        path=str(path),
        peers=content.count("[Peer]"),
        uploaded_at=_uploaded_at(path),
        preview=_build_preview(content),
    )


@router.post("/wireguard", response_model=WireGuardUploadResponse)
async def upload_wireguard_config(
    payload: WireGuardUploadRequest,
) -> WireGuardUploadResponse:
    """Validate and persist a wg0.conf file with restrictive permissions."""
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="WireGuard configuration is empty")
    if "[Interface]" not in content:
        raise HTTPException(
            status_code=400,
            detail="Invalid WireGuard configuration: missing [Interface] section",
        )
    if "PrivateKey" not in content:
        raise HTTPException(
            status_code=400,
            detail="Invalid WireGuard configuration: missing PrivateKey entry",
        )

    path = _config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.tmp")
        temp_path.write_text(f"{content}\n", encoding="utf-8")
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
    except OSError as ex:
        raise HTTPException(
            status_code=500,
            detail=f"Could not write WireGuard configuration: {ex}",
        ) from ex

    return WireGuardUploadResponse(
        configured=True,
        peers=content.count("[Peer]"),
        uploaded_at=_uploaded_at(path),
    )


@router.delete("/wireguard", response_model=WireGuardStatusResponse)
async def delete_wireguard_config() -> WireGuardStatusResponse:
    """Remove the stored WireGuard configuration, if any."""
    path = _config_path()
    if path.is_file():
        path.unlink()
    return WireGuardStatusResponse(configured=False, path=str(path))
