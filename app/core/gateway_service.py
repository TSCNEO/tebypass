"""WireGuard client configuration upload and inspection endpoints."""

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import settings
from app.core.service_errors import ServiceError
from app.core.wireguard_config import validate_config
from app.models.schemas import (
    WireGuardStatusResponse,
    WireGuardUploadRequest,
    WireGuardUploadResponse,
)

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


async def upload_wireguard_config(payload: WireGuardUploadRequest) -> WireGuardUploadResponse:
    """Validate and atomically save before asking the sidecar to apply the file."""
    from app.core.engine import ROUTING_LOCK

    try:
        content = validate_config(payload.content)
    except ValueError as ex:
        raise ServiceError(status_code=400, detail=str(ex)) from ex
    async with ROUTING_LOCK:
        path = _config_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, name = tempfile.mkstemp(dir=path.parent, prefix=".wg-")
            temp_path = Path(name)
            try:
                with os.fdopen(fd, "w") as handle:
                    handle.write(content)
                os.replace(temp_path, path)
            finally:
                temp_path.unlink(missing_ok=True)
        except OSError as ex:
            raise ServiceError(
                status_code=500, detail="Could not persist WireGuard configuration"
            ) from ex
        applied = await _apply_if_gateway()
        return WireGuardUploadResponse(
            applied=applied,
            configured=True,
            peers=content.count("[Peer]"),
            uploaded_at=_uploaded_at(path),
        )


async def delete_wireguard_config() -> WireGuardStatusResponse:
    """Remove the stored WireGuard configuration, if any."""
    from app.core.engine import ROUTING_LOCK

    async with ROUTING_LOCK:
        path = _config_path()
        path.unlink(missing_ok=True)
        await _apply_if_gateway()
        return WireGuardStatusResponse(configured=False, path=str(path))


async def _apply_if_gateway() -> bool:
    if settings.router_driver != "virtual_gateway":
        return False
    from app.drivers import get_driver
    from app.drivers.virtual_gateway import VirtualGatewayDriver

    driver = get_driver()
    if not isinstance(driver, VirtualGatewayDriver):
        raise ServiceError(status_code=409, detail="Gateway configuration changed")
    try:
        await driver.reload_wireguard()
    except RuntimeError as ex:
        raise ServiceError(status_code=502, detail=str(ex)) from ex

    return True
