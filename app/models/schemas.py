"""Pydantic request and response schemas."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class TargetBase(BaseModel):
    """Base schema for target data."""

    value: str = Field(
        ..., description="FQDN (e.g. sport-stream.example.org) or CIDR (e.g. 198.51.100.0/24)"
    )
    destination_type: Literal["fqdn", "cidr"] | None = Field(
        default=None, description="Explicit type or auto-detected if None"
    )
    description: str | None = Field(default=None, description="Optional administrative note")
    enabled: bool = Field(default=True, description="Whether the target is actively routed")
    folder_id: str | None = Field(default=None, description="Optional folder / list identifier")


class TargetCreate(TargetBase):
    """Request payload for creating a new target."""

    pass


class TargetUpdate(BaseModel):
    """Partial update payload for an existing target."""

    value: str | None = Field(default=None, description="New FQDN or CIDR value")
    description: str | None = Field(default=None, description="New administrative note")
    enabled: bool | None = Field(default=None, description="Enable or disable routing")
    folder_id: str | None = Field(default=None, description="Folder id, or null to unassign")


class BulkTargetImportRequest(BaseModel):
    """Bulk import payload: one entry per line, comments/blank lines ignored."""

    entries: list[str] = Field(default_factory=list, description="Raw lines to import")
    folder_id: str | None = Field(default=None, description="Folder assigned to new targets")
    description: str | None = Field(default=None, description="Shared description override")


class BulkTargetImportResponse(BaseModel):
    """Outcome report of a bulk import operation."""

    created: int
    skipped: int
    invalid: list[str] = Field(default_factory=list)


class TargetResolveResponse(BaseModel):
    """Immediate DNS resolution result for a single target."""

    value: str
    ips: list[str] = Field(default_factory=list)


class TargetResponse(TargetBase):
    """Response payload representing a registered target."""

    id: str
    feed_id: str | None = None
    folder_name: str | None = None
    created_at: datetime
    resolved_count: int = 0
    resolved_ips: list[str] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class FolderCreate(BaseModel):
    """Request payload for creating a folder / personal list."""

    name: str = Field(min_length=1, max_length=128, description="Folder display name")
    color: str = Field(default="#f59e0b", max_length=16, description="Hex accent color")


class FolderUpdate(BaseModel):
    """Partial update payload for a folder."""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    color: str | None = Field(default=None, max_length=16)


class FolderResponse(BaseModel):
    """Response payload representing a folder and its target count."""

    id: str
    name: str
    color: str
    targets_count: int = 0
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class FeedBase(BaseModel):
    """Base schema for remote feed configuration."""

    name: str = Field(..., description="Human-readable feed label")
    url: str = Field(..., description="Remote HTTPS/HTTP endpoint URL")
    format: str = Field(default="json", description="Feed format: json, plaintext, stopliga")
    enabled: bool = Field(default=True, description="Whether feed is queried during scheduled sync")


class FeedCreate(FeedBase):
    """Request payload for creating a new feed."""

    pass


class FeedResponse(FeedBase):
    """Response payload representing a registered feed."""

    id: str
    last_fetched_at: datetime | None = None
    created_at: datetime
    targets_count: int = 0

    model_config = {"from_attributes": True}


class SyncTriggerRequest(BaseModel):
    """Request payload to manually trigger a route reconciliation."""

    force: bool = Field(default=False, description="Force re-resolution ignoring cache")
    pin: str | None = Field(default=None, description="Master PIN for authorization")


class SyncResponse(BaseModel):
    """Response payload detailing the outcome of a sync run."""

    driver_name: str
    added_count: int
    removed_count: int
    total_active: int
    duration_ms: float
    success: bool
    error_message: str | None = None


class DriverStatusResponse(BaseModel):
    """Status details for the active router driver."""

    driver_name: str
    health: str
    active_routes_count: int
    last_sync: datetime | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class SystemStatusResponse(BaseModel):
    """Overall system health and telemetry response."""

    status: str
    version: str
    driver: DriverStatusResponse
    total_targets: int
    total_resolved_ips: int
    active_routes: int
    sliding_window_seconds: int


class PinVerifyRequest(BaseModel):
    """Request payload for master PIN validation."""

    pin: str


class PinVerifyResponse(BaseModel):
    """Response payload for master PIN validation."""

    success: bool
    token: str | None = None


class RouterTestResponse(BaseModel):
    """Result of an on-demand router driver health probe."""

    healthy: bool
    driver: str
    details: dict[str, Any] = Field(default_factory=dict)


class RouterPurgeResponse(BaseModel):
    """Result of an on-demand router route/rule purge."""

    success: bool
    driver: str
    message: str


class UniFiVPNNetwork(BaseModel):
    """UniFi VPN Client (WireGuard / OpenVPN) network definition."""

    id: str
    name: str
    type: str = "wireguard-client"


class WireGuardStatusResponse(BaseModel):
    """State of the uploaded WireGuard client configuration."""

    configured: bool
    path: str
    peers: int = 0
    uploaded_at: str | None = None
    preview: str = ""


class WireGuardUploadRequest(BaseModel):
    """Raw wg0.conf content uploaded from the web UI."""

    content: str = Field(description="Full WireGuard configuration text")


class WireGuardUploadResponse(BaseModel):
    """Result of a successful WireGuard configuration upload."""

    configured: bool
    peers: int
    uploaded_at: str
