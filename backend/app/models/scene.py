"""Persistence for the validated FR06 scene aggregate."""

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel


class SceneRecord(SQLModel, table=True):
    """Keep a scene together so manual and measured inputs round-trip identically.

    The versioned Pydantic contract validates the document at the API boundary.
    Separate identity and revision columns support atomic optimistic updates.
    This table deliberately does not replace the pre-existing draft Room table.
    """

    __tablename__ = "scenes"

    scene_id: str = Field(primary_key=True)
    revision: int
    scene_json: str = Field(sa_column=Column(Text, nullable=False))
    # Account that owns the space (auth_users.id); empty for spaces made before accounts existed.
    owner_id: str | None = Field(default=None, index=True)
