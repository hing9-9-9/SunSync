"""FR06 scene storage and validation; sunlight/control belong to their engines."""

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import CursorResult, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.db import get_session
from app.models.scene import SceneRecord
from app.scene_cli import schema_document
from app.schemas.scene import Scene

router = APIRouter(tags=["scenes"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.get("/scene-schema")
def get_scene_schema() -> dict[str, Any]:
    """Return the same JSON Schema used for manual/ArUco interchange."""
    return schema_document()


@router.post("/scenes/validate", response_model=Scene)
def validate_scene(scene: Scene) -> Scene:
    """Validate a scene without persisting it."""
    return scene


@router.post("/scenes", response_model=Scene, status_code=status.HTTP_201_CREATED)
def create_scene(scene: Scene, session: SessionDependency) -> Scene:
    """Import a complete scene, preserving any positive initial revision."""
    record = SceneRecord(
        scene_id=scene.scene_id,
        revision=scene.revision,
        scene_json=scene.model_dump_json(),
    )
    session.add(record)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A scene with this scene_id already exists.",
        ) from exc
    return scene


@router.get("/scenes", response_model=list[Scene])
def list_scenes(
    session: SessionDependency,
    offset: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[Scene]:
    """List full scenes in stable scene_id order with bounded pagination."""
    records = session.exec(
        select(SceneRecord).order_by(col(SceneRecord.scene_id)).offset(offset).limit(limit)
    ).all()
    return [Scene.model_validate_json(record.scene_json) for record in records]


@router.get("/scenes/{scene_id}", response_model=Scene)
def get_scene(scene_id: str, session: SessionDependency) -> Scene:
    record = session.get(SceneRecord, scene_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scene not found.")
    return Scene.model_validate_json(record.scene_json)


@router.put("/scenes/{scene_id}", response_model=Scene)
def replace_scene(scene_id: str, scene: Scene, session: SessionDependency) -> Scene:
    """Replace a scene only when its revision advances exactly once.

    The revision predicate and write are one SQL statement, preventing two
    clients that read the same revision from overwriting each other's changes.
    """
    if scene.scene_id != scene_id:
        raise HTTPException(
            status_code=422,
            detail="Payload scene_id must match the URL scene_id.",
        )

    result = cast(
        CursorResult[Any],
        session.execute(
            update(SceneRecord)
            .where(
                col(SceneRecord.scene_id) == scene_id,
                col(SceneRecord.revision) == scene.revision - 1,
            )
            .values(revision=scene.revision, scene_json=scene.model_dump_json())
        ),
    )
    if result.rowcount != 1:
        session.rollback()
        if session.get(SceneRecord, scene_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scene not found.")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Revision conflict: read the current scene and submit its revision plus one.",
        )
    session.commit()
    return scene
