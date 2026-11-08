"""FR06 scene storage and validation; sunlight/control belong to their engines."""

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import CursorResult, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.db import get_session
from app.models.scene import SceneRecord
from app.scene_cli import schema_document
from app.schemas.registration import RegisterFurnitureRequest
from app.schemas.scene import Scene
from app.services.stored_scene import load_stored_scene
from app.ui_scene import sync_ui_record

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
    return [load_stored_scene(record.scene_json) for record in records]


@router.get("/scenes/{scene_id}", response_model=Scene)
def get_scene(scene_id: str, session: SessionDependency) -> Scene:
    record = session.get(SceneRecord, scene_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scene not found.")
    return load_stored_scene(record.scene_json)


@router.post("/scenes/{scene_id}/registrations", response_model=Scene)
def register_furniture(
    scene_id: str, request: RegisterFurnitureRequest, session: SessionDependency
) -> Scene:
    """Append measured furniture atomically, preserving the existing room and site.

    All coordinates must already use this room's confirmed ENU origin/north/floor.
    Reusing the same marker is allowed only with identical calibration/placement.
    Geometry and provenance are validated together before any database write.
    """
    current = get_scene(scene_id, session)
    if current.revision != request.expected_revision:
        raise HTTPException(status_code=409, detail="Revision conflict: reload the scene.")
    fragment = request.fragment
    calibration = current.registration_calibration
    if (
        calibration is None
        or calibration.calibration_id != fragment.calibration_id
        or len(fragment.markers) != 1
        or fragment.markers[0] != calibration.marker
        or any(o.calibration_id != calibration.calibration_id for o in fragment.observations)
    ):
        raise HTTPException(
            status_code=422,
            detail="Register and confirm the matching board/ENU/floor calibration in the scene first.",
        )
    if any(obj.room_id != current.room.room_id for obj in fragment.occluders):
        raise HTTPException(
            status_code=422, detail="Furniture must use the existing room ENU frame."
        )
    data = current.model_dump(mode="json")
    data["revision"] += 1
    if data["scenario"] is not None:
        data["scenario"]["scene_revision"] = data["revision"]
    known = {marker.marker_id: marker for marker in current.markers}
    incoming_ids: set[str] = set()
    for marker in fragment.markers:
        if marker.marker_id in incoming_ids:
            raise HTTPException(status_code=422, detail="Duplicate registration marker ID.")
        incoming_ids.add(marker.marker_id)
        if marker.marker_id in known:
            if marker != known[marker.marker_id]:
                raise HTTPException(status_code=422, detail="Existing marker placement differs.")
        else:
            data["markers"].append(marker.model_dump(mode="json"))
    data["observations"].extend(o.model_dump(mode="json") for o in fragment.observations)
    data["occluders"].extend(o.model_dump(mode="json") for o in fragment.occluders)
    try:
        merged = Scene.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return replace_scene(scene_id, merged, session)


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

    stored = session.get(SceneRecord, scene_id)
    previous = load_stored_scene(stored.scene_json) if stored is not None else None
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
    if previous is not None:
        sync_ui_record(session, previous, scene)
    session.commit()
    return scene
