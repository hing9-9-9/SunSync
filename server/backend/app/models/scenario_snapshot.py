"""Persistence for precomputed daily sunpath, FR-02 shading, and FR-04 adaptive scenarios."""

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel


class ScenarioSnapshotRecord(SQLModel, table=True):
    """Cached prediction and shading plans keyed by scene revision and request window.

    Separating the slow 3D raycast computation from request-time reads allows local scene
    edits and 10-minute global rolling updates to refresh scenarios once while UI/device
    reads answer from SQLite in milliseconds.
    """

    __tablename__ = "scenario_snapshots"

    snapshot_key: str = Field(primary_key=True)
    scene_id: str = Field(index=True)
    scene_revision: int = Field(index=True)
    start_time: str
    duration_minutes: int
    interval_seconds: int
    time_bucket: str = Field(index=True)
    weather_fingerprint: str
    sunpath_summary_json: str = Field(sa_column=Column(Text, nullable=False))
    shading_plan_json: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    adaptive_plan_json: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    computed_at: str
