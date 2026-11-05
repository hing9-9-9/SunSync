"""Atomic UI projection + FR06 storage, on the same development database."""

from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import CursorResult, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.db import get_session
from app.models.scene import SceneRecord
from app.schemas.scene import Scene
from app.ui_scene import DeletedSpaceRecord, UISpace, UISpaceRecord, canonical_id, to_scene

router = APIRouter(prefix="/ui/spaces", tags=["ui-spaces"])
DB = Annotated[Session, Depends(get_session)]


def checked_scene(space: UISpace, revision: int, created_at: str | None = None) -> Scene:
    try:
        return to_scene(space, revision, created_at)
    except ValidationError as error:
        raise HTTPException(
            422, detail=[{key: e[key] for key in ("loc", "msg", "type")} for e in error.errors()]
        ) from error


def keep_paired_markers(scene: Scene, previous: Scene) -> Scene:
    """FR-05 pairing writes markers the UI never sends; a UI save keeps them while their window is unchanged."""
    windows = {w.window_id: w.vertices_m for w in scene.windows}
    before = {w.window_id: w.vertices_m for w in previous.windows}
    kept = [
        m
        for m in previous.markers
        if m.window_id is None
        or (m.window_id in windows and windows[m.window_id] == before.get(m.window_id))
    ]
    if not kept:
        return scene
    try:
        return Scene.model_validate(
            {**scene.model_dump(mode="json"), "markers": [m.model_dump(mode="json") for m in kept]}
        )
    except ValidationError:
        # A marker that no longer fits the regenerated film grid must be paired again.
        return scene


@router.get("")
def list_spaces(
    session: DB,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> dict[str, Any]:
    scenes = session.exec(
        select(SceneRecord).order_by(col(SceneRecord.scene_id)).offset(offset).limit(limit)
    ).all()
    spaces, unavailable = [], []
    for scene in scenes:
        record = session.exec(
            select(UISpaceRecord).where(UISpaceRecord.scene_id == scene.scene_id)
        ).first()
        if record is None or record.scene_revision != scene.revision:
            unavailable.append(
                {
                    "scene_id": scene.scene_id,
                    "name": Scene.model_validate_json(scene.scene_json).room.name,
                    "reason": "외부 형식이거나 다른 모듈에서 수정된 공간입니다.",
                }
            )
        else:
            spaces.append(UISpace.model_validate_json(record.payload_json).model_dump())
    deleted = [row.space_id for row in session.exec(select(DeletedSpaceRecord)).all()]
    return {
        "spaces": spaces,
        "unavailable": unavailable,
        "deleted": deleted,
        "has_more": len(scenes) == limit,
    }


@router.get("/{space_id}", response_model=UISpace)
def get_space(space_id: str, session: DB) -> UISpace:
    record = session.get(UISpaceRecord, space_id)
    if record is None:
        raise HTTPException(404, "서버 공간을 찾을 수 없습니다.")
    scene = session.get(SceneRecord, record.scene_id)
    if scene is None or record.scene_revision != scene.revision:
        raise HTTPException(
            409, "다른 모듈에서 변경된 공간입니다. 공통 Scene의 변경 내용을 먼저 확인해 주세요."
        )
    return UISpace.model_validate_json(record.payload_json)


@router.post("", response_model=UISpace, status_code=201)
def create_space(space: UISpace, session: DB) -> UISpace:
    if space.serverRevision is not None:
        raise HTTPException(409, "이미 서버에 저장한 공간입니다. 최신 버전으로 수정해 주세요.")
    if session.get(DeletedSpaceRecord, space.id) is not None:
        raise HTTPException(409, "삭제된 공간입니다. 새 공간으로 등록해 주세요.")
    scene = checked_scene(space, 1)
    saved = space.model_copy(
        update={
            "serverRevision": 1,
            "serverSceneId": scene.scene_id,
            "savedAt": datetime.now(UTC).isoformat(),
        }
    )
    session.add(
        SceneRecord(scene_id=scene.scene_id, revision=1, scene_json=scene.model_dump_json())
    )
    session.add(
        UISpaceRecord(
            space_id=space.id,
            scene_id=scene.scene_id,
            scene_revision=1,
            payload_json=saved.model_dump_json(),
        )
    )
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            409, "같은 공간이 이미 등록되어 있습니다. 서버 목록을 다시 불러와 주세요."
        ) from error
    return saved


@router.put("/{space_id}", response_model=UISpace)
def replace_space(space_id: str, space: UISpace, session: DB) -> UISpace:
    if space_id != space.id:
        raise HTTPException(422, "URL과 본문의 공간 ID가 다릅니다.")
    record = session.get(UISpaceRecord, space_id)
    existing = session.get(SceneRecord, canonical_id("scene", space_id))
    if record is None or existing is None:
        raise HTTPException(404, "서버 공간을 찾을 수 없습니다.")
    if space.serverRevision != existing.revision or record.scene_revision != existing.revision:
        raise HTTPException(
            409, "다른 곳에서 수정된 공간입니다. 최신 내용을 불러온 뒤 다시 수정해 주세요."
        )
    revision = existing.revision + 1
    previous = Scene.model_validate_json(existing.scene_json)
    scene = keep_paired_markers(checked_scene(space, revision, previous.created_at), previous)
    saved = space.model_copy(
        update={
            "serverRevision": revision,
            "serverSceneId": scene.scene_id,
            "savedAt": datetime.now(UTC).isoformat(),
        }
    )
    result = cast(
        CursorResult[Any],
        session.execute(
            update(SceneRecord)
            .where(
                col(SceneRecord.scene_id) == scene.scene_id,
                col(SceneRecord.revision) == space.serverRevision,
            )
            .values(revision=revision, scene_json=scene.model_dump_json())
        ),
    )
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(409, "저장 버전이 변경됐습니다. 최신 내용을 다시 불러와 주세요.")
    record.scene_revision = revision
    record.payload_json = saved.model_dump_json()
    session.add(record)
    session.commit()
    return saved


@router.delete("/{space_id}", status_code=204)
def delete_space(space_id: str, session: DB) -> None:
    """Removes the space and its Scene; repeating it is harmless."""
    record = session.get(UISpaceRecord, space_id)
    scene = session.get(SceneRecord, canonical_id("scene", space_id))
    for row in (record, scene):
        if row is not None:
            session.delete(row)
    if session.get(DeletedSpaceRecord, space_id) is None:
        session.add(DeletedSpaceRecord(space_id=space_id, deleted_at=datetime.now(UTC).isoformat()))
    session.commit()
