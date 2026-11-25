"""Persistence for virtual smart film states and chronological application logs."""

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel


class FilmStateRecord(SQLModel, table=True):
    """Current state of a smart film device for a given (scene_id, window_id)."""

    __tablename__ = "film_states"

    state_key: str = Field(primary_key=True)  # formatted as f"{scene_id}:{window_id}"
    scene_id: str = Field(index=True)
    window_id: str = Field(index=True)
    film_type: str = "PDLC"
    film_rows: int = 10
    film_cols: int = 10
    cells_json: str = Field(sa_column=Column(Text, nullable=False))
    marker_mode_active: bool = False
    marker_code_id: int | None = None
    marker_start_row: int | None = None
    marker_start_col: int | None = None
    marker_backup_json: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    active_plan_id: str | None = None
    active_plan_json: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    desired_version: int = 0
    reported_version: int = 0
    reported_cells_json: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    sync_status: str = "local_only"
    last_command_id: str | None = None
    last_synced_at: str | None = None
    hub_device_id: str | None = None
    sync_error: str | None = None
    updated_at: str


class FilmApplicationLogRecord(SQLModel, table=True):
    """Durable append-only audit log of cell opacity transitions and schedule applications."""

    __tablename__ = "film_application_logs"

    log_id: int | None = Field(default=None, primary_key=True)
    scene_id: str = Field(index=True)
    window_id: str = Field(index=True)
    timestamp: str = Field(index=True)  # ISO 8601 with timezone offset
    row: int
    col: int
    previous_opacity: float
    new_opacity: float
    previous_state: str  # "transparent", "opaque", "dimmed"
    new_state: str  # "transparent", "opaque", "dimmed"
    plan_id: str | None = None
    status: str  # "applied", "target_unmet", "unknown_intensity"
    note: str = ""
    reasons_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    command_id: str | None = Field(default=None, index=True)
    delivery_status: str = "local_only"
    hub_ack_at: str | None = None
    created_at: str


class HubCommandRecord(SQLModel, table=True):
    """Transactional Outbox record for backend-to-hub film and ThinQ control commands."""

    __tablename__ = "hub_commands"

    command_id: str = Field(primary_key=True)
    scene_id: str = Field(index=True)
    window_id: str = Field(index=True)
    operation: str
    desired_version: int = 0
    payload_json: str = Field(sa_column=Column(Text, nullable=False))
    status: str = Field(default="pending", index=True)
    attempts: int = 0
    next_retry_at: str | None = None
    last_error: str | None = None
    created_at: str
    updated_at: str
