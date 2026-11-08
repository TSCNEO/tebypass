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


class TargetCreate(TargetBase):
    """Request payload for creating a new target."""

    pass


class TargetResponse(TargetBase):
    """Response payload representing a registered target."""

    id: str
    feed_id: str | None = None
    created_at: datetime
    resolved_count: int = 0

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
