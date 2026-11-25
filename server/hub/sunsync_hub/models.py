"""SQLModel persistence models for Hub Window Shadows and Idempotent Command Receipts."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel


def utc_now_iso() -> str:
    """Return current UTC timestamp in canonical ISO 8601 format."""
    now = datetime.now(UTC)
    if now.microsecond == 0:
        return now.strftime("%Y-%m-%dT%H:%M:%SZ")
    return now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class HubWindowShadow(SQLModel, table=True):
    """Authoritative Device Shadow stored on the Film & ThinQ ON Hub."""

    __tablename__ = "hub_window_shadows"

    shadow_key: str = Field(primary_key=True)
    scene_id: str = Field(index=True)
    window_id: str = Field(index=True)
    hardware_mode: str = Field(default="PDLC")
    rows: int = Field(default=10)
    cols: int = Field(default=10)
    desired_version: int = Field(default=0)
    reported_version: int = Field(default=0)
    desired_base_cells_json: str = Field(sa_column=Column(Text, nullable=False))
    desired_marker_json: str = Field(sa_column=Column(Text, nullable=False))
    reported_base_cells_json: str = Field(sa_column=Column(Text, nullable=False))
    reported_marker_json: str = Field(sa_column=Column(Text, nullable=False))
    effective_cells_json: str = Field(sa_column=Column(Text, nullable=False))
    state_digest: str = Field(default="")
    sync_status: str = Field(default="in_sync")
    rejection_reason: str | None = Field(default=None)
    updated_at: str = Field(default_factory=utc_now_iso)


class HubCommandReceipt(SQLModel, table=True):
    """Strict idempotency receipt cache keyed by command_id (Idempotency-Key)."""

    __tablename__ = "hub_command_receipts"

    command_id: str = Field(primary_key=True)
    scene_id: str = Field(index=True)
    window_id: str = Field(index=True)
    operation: str
    desired_version: int = Field(default=0)
    status: str
    response_json: str = Field(sa_column=Column(Text, nullable=False))
    created_at: str = Field(default_factory=utc_now_iso)
